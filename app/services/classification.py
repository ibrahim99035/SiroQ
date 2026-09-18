"""Canonical-field synonym dictionary and fuzzy/content classification.

Layer 1 scores headers with rapidfuzz against hard-coded synonyms (English +
Arabic + CJK terms). Layer 2 infers from content (dates, numerics, low
cardinality categories) for anything under the 70% confidence threshold.

There is intentionally NO LLM-fallback layer — it is deferred (see
KNOWN_ISSUES.md), so no dead/stubbed code exists here.
"""
from rapidfuzz import fuzz
import re
import openpyxl

CANONICAL_FIELD_SYNONYMS = {
    "expiry_date": ["exp", "expiry", "use by", "use-by", "datum ur",
                    "date d'utilisation", "日期", "تاريخ الانتهاء"],
    "receipt_date": ["received", "receipt date", "date received", "入庫日"],
    "sale_timestamp": ["sale date", "transaction date", "sold date",
                       "transaction timestamp", "transaction date",
                       "تاريخ البيع", "التاريخ"],
    "quantity": ["qty", "quantity", "amount", "count", "الكمية"],
    "unit_price": ["unit price", "price per unit", "unit cost",
                   "سعر الوحدة", "السعر"],
    "total_amount": ["total amount", "total sales", "grand total",
                     "الإجمالي", "المبلغ"],
    "payment_method": ["payment", "pay method", "mode of payment",
                       "طريقة الدفع"],
    "prescriber": ["prescriber", "doctor", "physician", " prescribing",
                   "الطبيب"],
    "batch_id": ["batch", "lot", "lot number", "الدفعة", "رقم الدفعة"],
    "product_name": ["product", "drug name", "medicine name", "drug",
                     "اسم المنتج", "المنتج"],
}

CONF_CONFIRMED = 90
CONF_UNCERTAIN = 70


def score_headers(headers, field):
    best = 0
    for h in headers:
        s = fuzz.token_sort_ratio(str(h).lower(), field.lower())
        if s > best:
            best = s
    return best


def score_content(values):
    if not values:
        return 0.0
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return 0.0
    distinct = len({str(v).lower().strip() for v in non_null})
    ratio = distinct / len(non_null)
    if ratio < 0.1 and len(non_null) >= 3:
        return 85.0
    date_re = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}")
    dates = sum(1 for v in non_null if date_re.search(str(v)))
    if dates / len(non_null) > 0.6:
        return 90.0
    num_re = re.compile(r"^-?\d+(\.\d+)?$")
    nums = sum(1 for v in non_null if num_re.match(str(v).strip()))
    if nums / len(non_null) > 0.6:
        return 88.0
    return 50.0


def classify_file(file_path: str) -> dict:
    """Return header + content scores and a suggested mapping for a file."""
    import pandas as pd

    if file_path.lower().endswith(".csv"):
        df = pd.read_csv(file_path)
    elif file_path.lower().endswith((".xls", ".xlsx")):
        df = pd.read_excel(file_path)
        # merged-header heuristic: a title row above the real header shows up
        # as "Unnamed: N" columns; shift to the header row below it.
        if any(str(c).startswith("Unnamed:") for c in df.columns):
            df = pd.read_excel(file_path, header=1)
    else:
        return {"error": f"Unsupported file type: {file_path}"}

    return _classify_dataframe(df)


def _classify_dataframe(df: "pd.DataFrame") -> dict:
    """Classify a dataframe and return mapping suggestions."""
    headers = [str(h) for h in df.columns.tolist()]
    values = {h: df[h].tolist() for h in df.columns}

    field_scores = {}
    suggested = {}
    for field, syns in CANONICAL_FIELD_SYNONYMS.items():
        best_score = 0.0
        best_header = None
        for token in syns:
            for h in headers:
                s = fuzz.token_sort_ratio(h.lower(), token.lower())
                if s > best_score:
                    best_score = s
                    best_header = h
        field_scores[field] = round(best_score, 1)
        suggested[field] = best_header

    content_scores = {}
    for field in CANONICAL_FIELD_SYNONYMS:
        if field_scores[field] < CONF_UNCERTAIN:
            best_c = 0.0
            best_h = None
            for h in headers:
                c = score_content(values.get(h, []))
                if c > best_c:
                    best_c = c
                    best_h = h
            content_scores[field] = round(best_c, 1)
            if best_c > field_scores[field]:
                suggested[field] = best_h
        else:
            content_scores[field] = field_scores[field]

    final = {}
    for field in CANONICAL_FIELD_SYNONYMS:
        score = max(field_scores[field], content_scores.get(field, 0))
        if score >= CONF_CONFIRMED:
            status = "confirmed"
        elif score >= CONF_UNCERTAIN:
            status = "uncertain"
        else:
            status = "unconfirmed"
        final[field] = {
            "score": score,
            "status": status,
            "sample_values": [str(v) for v in (values.get(suggested[field], [])[:3])]
            if suggested[field] else [],
            "suggested_mapping": suggested[field],
        }

    return {
        "headers": headers,
        "field_scores": final,
        "canonical_fields": list(CANONICAL_FIELD_SYNONYMS.keys()),
    }


def classify_excel_sheets(file_path: str) -> dict:
    """Classify all sheets in an Excel file. Returns dict of sheet_name -> classification."""
    wb = openpyxl.load_workbook(file_path, read_only=True)
    results = {}
    import pandas as pd
    for sheet_name in wb.sheetnames:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        if any(str(c).startswith("Unnamed:") for c in df.columns):
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=1)
        results[sheet_name] = _classify_dataframe(df)
    return results


def classify_zip(file_path: str) -> dict:
    """Classify all supported files in a zip archive."""
    import zipfile
    import pandas as pd
    from io import BytesIO

    results = {}
    with zipfile.ZipFile(file_path, 'r') as zf:
        for name in zf.namelist():
            if name.endswith('/'):
                continue
            ext = name.lower().split('.')[-1]
            if ext not in ('csv', 'xlsx', 'xls'):
                continue
            with zf.open(name) as f:
                content = f.read()
            if ext == 'csv':
                df = pd.read_csv(BytesIO(content))
            else:
                df = pd.read_excel(BytesIO(content))
                if any(str(c).startswith("Unnamed:") for c in df.columns):
                    df = pd.read_excel(BytesIO(content), header=1)
            results[name] = _classify_dataframe(df)
    return results