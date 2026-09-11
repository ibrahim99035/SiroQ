from rapidfuzz import fuzz
import re


# Layer 1: header fuzzy match
CANONICAL_FIELD_SYNONYMS = {
    "expiry_date": ["exp", "expiry", "use by", "use-by", "datum ur", "date d'utilisation", "日期", "تاريخ الانتهاء"],
    "receipt_date": ["received", "receipt date", "date received", "入庫日"],
    "sale_timestamp": ["sale date", "transaction date", "sold date", "transaction timestamp"],
    "quantity": ["qty", "quantity", "amount", "count"],
    "unit_price": ["unit price", "price per unit", "unit cost", "unit cost"],
    "total_amount": ["total amount", "total sales", "grand total"],
    "payment_method": ["payment", "pay method", "mode of payment"],
    "prescriber": ["prescriber", "doctor", "physician", " prescribing"],
    "batch_id": ["batch", "lot", "lot number"],
    "product_name": ["product", "drug name", "medicine name"],
}


def score_headers(headers: list[str], field: str) -> float:
    """Score a single header against a canonical field using rapidfuzz token_sort_ratio."""
    best = 0
    for h in headers:
        score = fuzz.token_sort_ratio(h.lower(), field.lower())
        if score > best:
            best = score
    return best


def score_content(values: list) -> float:
    """Layer 2: content-based inference confidence score (0-100)."""
    if not values:
        return 0
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return 0
    ratio = len(set(str(v).lower().strip() for v in non_null)) / len(non_null)
    # Low cardinality = likely categorical
    if ratio < 0.1 and len(non_null) >= 3:
        return 85.0
    # Check for date patterns
    date_pattern = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}")
    date_count = sum(1 for v in non_null if date_pattern.search(str(v)))
    if date_count / len(non_null) > 0.6:
        return 90.0
    # Check for numeric with decimal
    numeric_count = sum(
        1 for v in non_null
        if isinstance(v, (int, float))
        or (isinstance(str(v), str) and re.match(r"^-?\d+\.\d+", str(v)))
    )
    if numeric_count / len(non_null) > 0.6:
        return 88.0
    return 50.0


def classify_file(file_path: str, association_id: str) -> dict:
    """Classify an uploaded file: return header scores + content scores + suggestions."""
    import pandas as pd
    import os

    # Determine file type and read
    if file_path.endswith(".csv"):
        df = pd.read_csv(file_path)
    elif file_path.endswith((".xls", ".xlsx")):
        df = pd.read_excel(file_path)
    else:
        return {"error": f"Unsupported file type: {file_path}"}

    headers = df.columns.tolist()
    values_lists = {h: df[h].tolist() for h in headers if h in df.columns}

    result = {
        "headers": headers,
        "field_scores": {},
        "content_scores": {},
        "suggested_mapping": {},
        "unconfirmed": [],
    }

    # Layer 1: fuzzy header match
    for field in CANONICAL_FIELD_SYNONYMS:
        best_score = 0
        best_header = None
        for h in headers:
            score = score_headers([h], field)
            if score > best_score:
                best_score = score
                best_header = h
        result["field_scores"][field] = best_score
        if best_header:
            result["suggested_mapping"][field] = best_header

    # Layer 2: content-based inference for unconfirmed fields
    for field in CANONICAL_FIELD_SYNONYMS:
        score = result["field_scores"].get(field, 0)
        if score < 70:
            # Try content-based inference
            # Find the header with best content score
            best_content_score = 0
            best_header = None
            for h in headers:
                cv = values_lists.get(h, [])
                cs = score_content(cv)
                if cs > best_content_score:
                    best_content_score = cs
                    best_header = h
            content_score = best_content_score
        else:
            content_score = score

        final_score = max(score, content_score)
        result["content_scores"][field] = round(content_score, 1)

        # Update suggested mapping if content improved it
        if field in result["suggested_mapping"]:
            if content_score > result["field_scores"][field]:
                result["suggested_mapping"][field] = best_header or result["suggested_mapping"][field]

    # Fields not in our canonical list but present in the file
    for h in headers:
        if h not in [h for field_scores in result["field_scores"].values() for h in []]:
            result["unconfirmed"].append(h)

    # Compute confidence per field
    for field in CANONICAL_FIELD_SYNONYMS:
        final = result["field_scores"].get(field, 0)
        result["field_scores"][field] = {
            "score": final,
            "status": (
                "confirmed" if final >= 90
                else ("uncertain" if final >= 70 else "unconfirmed")
            ),
        }

    return result