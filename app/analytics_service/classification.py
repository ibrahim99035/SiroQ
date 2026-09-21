"""Canonical-field synonym dictionary and fuzzy/content classification.

Layer 1 scores headers with rapidfuzz against hard-coded synonyms (English +
Arabic + CJK terms). Layer 2 infers from content (dates, numerics, low
cardinality categories) for anything under the 70% confidence threshold.

There is intentionally NO LLM-fallback layer — it is deferred (see legacy
docs/KNOWN_ISSUES), so no dead/stubbed code exists here.

Ported unchanged from the original SiroQ service with the dataframe-level API
kept public (file/zip/sheet wrappers were dropped — the analysis service always
feeds normalized dataframes).
"""
from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

CANONICAL_FIELD_SYNONYMS = {
    "expiry_date": ["exp", "expiry", "use by", "use-by", "datum ur",
                    "date d'utilisation", "日期", "تاريخ الانتهاء",
                    "expiry_date", "expiry date", "exp date"],
    "receipt_date": ["received", "receipt date", "date received", "入庫日",
                     "receipt_date"],
    "sale_timestamp": ["sale date", "transaction date", "sold date",
                       "transaction timestamp", "transaction date",
                       "تاريخ البيع", "التاريخ", "sale_timestamp", "sale_datetime"],
    "transaction_ref": ["transaction id", "transaction_id", "receipt", "receipt id",
                        "receipt_id", "invoice", "invoice no", "invoice_no",
                        "transaction_ref", "ticket", "order id", "bill no",
                        "رقم العملية", "رقم الفاتورة"],
    "quantity": ["qty", "quantity", "amount", "count", "الكمية", "qty_sold",
                 "quantity_sold", "sold quantity", "sold_qty"],
    "unit_price": ["unit price", "price per unit", "unit cost",
                   "سعر الوحدة", "السعر", "unit_price", "price",
                   "selling_price", "sale price"],
    "total_amount": ["total amount", "total sales", "grand total",
                     "الإجمالي", "المبلغ", "total_amount", "total",
                     "total_revenue", "revenue", "net_revenue"],
    "payment_method": ["payment", "pay method", "mode of payment",
                       "طريقة الدفع", "payment_method", "payment type"],
    "prescriber": ["prescriber", "doctor", "physician", "prescribing",
                   "الطبيب", "prescriber_name"],
    "batch_id": ["batch", "lot", "lot number", "الدفعة", "رقم الدفعة",
                 "batch_id", "batch_no", "lot_no"],
    "product_name": ["product", "drug name", "medicine name", "drug",
                     "اسم المنتج", "المنتج", "product_name", "item", "item_name"],
}

CONF_CONFIRMED = 90
CONF_UNCERTAIN = 70

# Acceptable detected-value kinds per canonical field. The content-inference
# pass may only suggest a source column whose detected kind is in this set, so a
# date column can never be offered as a numeric amount (or vice versa).
FIELD_VALUE_KINDS = {
    "expiry_date": {"date"},
    "receipt_date": {"date"},
    "sale_timestamp": {"date"},
    "transaction_ref": {"category", "text"},
    "quantity": {"number"},
    "unit_price": {"number"},
    "total_amount": {"number"},
    "payment_method": {"category", "text"},
    "prescriber": {"category", "text"},
    "batch_id": {"category", "text"},
    "product_name": {"category", "text"},
}


def score_headers(headers, field):
    best = 0
    for h in headers:
        s = fuzz.token_sort_ratio(str(h).lower(), field.lower())
        if s > best:
            best = s
    return best


def detect_content(values):
    """Return ``(kinds, score)`` inferred from a column's values.

    ``kinds`` is a subset of {"date", "number", "category", "text"} and is what
    lets the classifier refuse a type-mismatched column; ``score`` keeps the
    historical confidence ladder (categorical 85 < numeric 88 < date 90).
    """
    if not values:
        return set(), 0.0
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return set(), 0.0

    date_re = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}")
    date_ratio = sum(1 for v in non_null if date_re.search(str(v))) / len(non_null)
    num_re = re.compile(r"^-?\d+(\.\d+)?$")
    num_ratio = sum(1 for v in non_null if num_re.match(str(v).strip())) / len(non_null)
    cat_ratio = len({str(v).lower().strip() for v in non_null}) / len(non_null)

    kinds = set()
    if date_ratio > 0.6:
        kinds.add("date")
    if num_ratio > 0.6:
        kinds.add("number")
    if cat_ratio < 0.1 and len(non_null) >= 3:
        kinds.add("category")
    if not kinds:
        kinds.add("text")

    if cat_ratio < 0.1 and len(non_null) >= 3:
        score = 85.0
    elif date_ratio > 0.6:
        score = 90.0
    elif num_ratio > 0.6:
        score = 88.0
    else:
        score = 50.0
    return kinds, score


def score_content(values):
    """Backwards-compatible scalar-only view of :func:`detect_content`."""
    return detect_content(values)[1]


def classify_dataframe(df: pd.DataFrame) -> dict:
    """Classify a dataframe and return mapping suggestions.

    Header (name) matches are authoritative, but a single source column may back
    only one canonical field. For any field left unresolved, content inference is
    allowed to pick a column only when its detected kind matches the field — this
    is what stops a weak header from grabbing an unrelated, higher-scoring column
    (e.g. mapping ``total_amount`` onto a date column).
    """
    headers = [str(h) for h in df.columns.tolist()]
    values = {h: df[h].tolist() for h in df.columns}

    # --- Phase A: header (name) matching --------------------------------
    header_scores = {}
    header_pick = {}
    for field, syns in CANONICAL_FIELD_SYNONYMS.items():
        best_score = 0.0
        best_header = None
        for token in syns:
            for h in headers:
                s = fuzz.token_sort_ratio(h.lower(), token.lower())
                if s > best_score:
                    best_score = s
                    best_header = h
        header_scores[field] = round(best_score, 1)
        header_pick[field] = best_header if best_score >= CONF_UNCERTAIN else None

    # Resolve collisions: keep the strongest field per source column and free
    # the losers so the content pass can try to place them elsewhere.
    winners = {}
    for field, col in header_pick.items():
        if col is None:
            continue
        if col not in winners or header_scores[field] > header_scores[winners[col]]:
            winners[col] = field
    claimed = {field: col for col, field in winners.items()}
    for field, col in header_pick.items():
        if col is not None and claimed.get(field) != col:
            header_pick[field] = None

    # --- Phase B: kind-aware content inference --------------------------
    content_scores = {}
    suggested = {field: claimed.get(field) for field in CANONICAL_FIELD_SYNONYMS}
    used_columns = {col for col in suggested.values() if col}
    for field in CANONICAL_FIELD_SYNONYMS:
        if suggested[field] is not None:
            content_scores[field] = header_scores[field]
            continue
        allowed = FIELD_VALUE_KINDS.get(field, set())
        best_c = 0.0
        best_h = None
        for h in headers:
            if h in used_columns:
                continue
            kinds, c = detect_content(values.get(h, []))
            if not (kinds & allowed):
                continue
            if c > best_c:
                best_c, best_h = c, h
        content_scores[field] = round(best_c, 1)
        if best_h is not None and best_c >= CONF_UNCERTAIN:
            # only surface a content-based suggestion that clears the review
            # threshold; a weak guess is reported as "unconfirmed" with no pick
            suggested[field] = best_h
            used_columns.add(best_h)

    # --- Phase C: final per-field status --------------------------------
    final = {}
    for field in CANONICAL_FIELD_SYNONYMS:
        score = max(header_scores[field], content_scores.get(field, 0))
        if score >= CONF_CONFIRMED:
            status = "confirmed"
        elif score >= CONF_UNCERTAIN:
            status = "uncertain"
        else:
            status = "unconfirmed"
        col = suggested[field]
        final[field] = {
            "score": score,
            "status": status,
            "sample_values": [str(v) for v in (values.get(col, [])[:3])] if col else [],
            "suggested_mapping": col,
        }

    return {
        "headers": headers,
        "field_scores": final,
        "canonical_fields": list(CANONICAL_FIELD_SYNONYMS.keys()),
    }