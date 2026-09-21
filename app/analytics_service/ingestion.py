"""File ingestion and schema detection for the analytical service."""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class IngestedFile:
    """Represents an ingested file with its data and metadata."""
    filename: str
    file_type: str  # csv, excel, json
    sheets: dict[str, pd.DataFrame]  # sheet_name -> dataframe (for excel)
    dataframe: pd.DataFrame  # primary dataframe (first sheet or only dataframe)
    columns: list[str]
    dtypes: dict[str, str]
    row_count: int
    sample_rows: list[dict[str, Any]]
    errors: list[str]
    notes: list[str] = None  # structural notes (e.g. report-table discovery)


def detect_file_type(filename: str) -> str:
    """Detect file type from extension."""
    ext = Path(filename).suffix.lower()
    if ext in {".csv"}:
        return "csv"
    if ext in {".xlsx", ".xls"}:
        return "excel"
    if ext in {".json", ".jsonl"}:
        return "json"
    raise ValueError(f"Unsupported file type: {ext}")


def read_file(content: bytes, filename: str) -> IngestedFile:
    """Read a file and return an IngestedFile with all sheets/dataframes."""
    file_type = detect_file_type(filename)
    errors = []
    notes = []

    try:
        if file_type == "csv":
            df = pd.read_csv(io.BytesIO(content))
            sheets = {"default": df}
        elif file_type == "excel":
            # Calamine reads exported workbooks whose style sheets are invalid
            # per OOXML (Crystal Reports writes font family > 14); openpyxl's
            # stylesheet parser rejects them wholesale, calamine ignores styles.
            raw_sheets = pd.read_excel(
                io.BytesIO(content), sheet_name=None, engine="calamine"
            )
            sheet_frames = {}
            for sheet_name, sheet_df in raw_sheets.items():
                extracted, sheet_notes = _extract_report_table(sheet_df)
                sheet_frames[sheet_name] = extracted
                notes.extend(f"{sheet_name}: {n}" for n in sheet_notes)
            sheets = sheet_frames
            df = list(sheets.values())[0] if sheets else pd.DataFrame()
        elif file_type == "json":
            try:
                data = json.loads(content.decode("utf-8"))
                if isinstance(data, list):
                    df = pd.DataFrame(data)
                elif isinstance(data, dict):
                    df = pd.DataFrame([data])
                else:
                    raise ValueError("JSON must be an array of objects or a single object")
            except json.JSONDecodeError:
                # Try JSONL
                lines = content.decode("utf-8").strip().split("\n")
                records = [json.loads(line) for line in lines if line.strip()]
                df = pd.DataFrame(records)
            sheets = {"default": df}
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
    except Exception as e:
        errors.append(f"Failed to read file: {e}")
        df = pd.DataFrame()
        sheets = {}

    # Normalize column names
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    for sheet_name, sheet_df in sheets.items():
        sheet_df.columns = [str(c).strip().lower().replace(" ", "_") for c in sheet_df.columns]

    columns = list(df.columns) if not df.empty else []
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()} if not df.empty else {}
    row_count = len(df) if not df.empty else 0
    sample_rows = df.head(5).to_dict("records") if not df.empty else []

    return IngestedFile(
        filename=filename,
        file_type=file_type,
        sheets=sheets,
        dataframe=df,
        columns=columns,
        dtypes=dtypes,
        row_count=row_count,
        sample_rows=sample_rows,
        errors=errors,
        notes=notes,
    )


# Arabic (Crystal Reports) header names mapped to canonical English fields.
_AR_HEADER_MAP = {
    "ن.الربح": "net_profit",
    "إجمالى التكلفة": "total_cost",
    "إجمالى س.البيع": "total_revenue",
    "سعر البيع": "selling_price",
    "الرصيد": "stock_balance",
    "كمية البيع": "quantity_sold",
    "الشركة": "manufacturer",
    "إسم الصنف": "product_name",
    "اسم الصنف": "product_name",
    "ك.الصنف": "product_code",
    "الكود": "product_code",
    "الاسم": "name",
    "الموظف": "staff",
    "التاريخ": "purchase_date",
    "الصافى": "net_amount",
    "ق.المرتجع": "returned_amount",
    "م.اضافية": "extra_amount",
    "خصم.ق": "discount_amount",
    "خصم.ن": "discount_pct",
    "ق.الفاتورة": "po_amount",
    "العدد": "item_count",
    "النوع": "purchase_type",
    "ر.الفاتورة": "po_number",
    "م": "line_seq",
    "الربح": "profit",
    "ض.م": "vat_amount",
    "التكلفة": "cost",
    "س.البيع": "selling_price",
    "م.البونص": "bonus_units",
    "ك.المرتجع": "returned_qty",
    "البونص": "bonus_qty",
    "الكمية": "quantity",
    "ت.ص": "expiry_date",
    "الوحدة": "unit",
    "الصيدلية": "pharmacy_name",
    "اسم المخزن": "store_name",
    "كود المخزن": "store_code",
}


def _is_report_header_only(df: pd.DataFrame) -> bool:
    """True when the frame looks like an exported report: sheet row 0 became
    scattered title cells and most columns are pandas 'unnamed' placeholders."""
    if df.empty or not len(df.columns):
        return False
    raw_names = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    unnamed = sum(1 for name in raw_names if name.startswith("unnamed:"))
    return unnamed / len(df.columns) >= 0.5


def _extract_report_table(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Locate the real table inside a Crystal Reports-style export.

    Such files scatter title/date rows above the actual header row, put the
    header labels one or two columns off their data columns, and interleave
    blank rows. We find the strongest all-string header row, bind each header
    label to the densest data column just below it, drop blank rows and
    empty columns, and map Arabic headers to canonical field names.

    Returns ``(table, notes)``.
    """
    notes: list[str] = []
    if not _is_report_header_only(df):
        return df, notes

    n_rows = len(df)
    probe_n = min(n_rows, 60)

    def _cell_is_str(value) -> bool:
        return isinstance(value, str)

    best_idx, best_n = -1, 0
    for i in range(probe_n):
        non_null = [v for v in df.iloc[i] if not _is_missing(v)]
        if len(non_null) >= 3 and all(_cell_is_str(v) for v in non_null):
            if len(non_null) > best_n:
                best_idx, best_n = i, len(non_null)
    if best_idx < 0:
        return df, notes

    header = df.iloc[best_idx]
    non_null_idx = [j for j, v in enumerate(header) if not _is_missing(v)]
    max_col = max(non_null_idx)
    body = df.iloc[best_idx + 1 :]

    mapped: list[tuple[str, int]] = []
    cursor = 0
    for j in non_null_idx:
        raw_name = str(header.iloc[j]).strip()
        name = _AR_HEADER_MAP.get(raw_name, raw_name.replace(" ", "_"))
        candidates = list(range(cursor, min(j, max_col) + 1))
        if not candidates:
            continue
        best = max(
            candidates,
            key=lambda c: (
                int(body.iloc[:30, c].notna().sum()),
                sum(
                    1
                    for v in body.iloc[:30, c]
                    if not _is_missing(v) and not isinstance(v, str)
                ),
                c == j,
            ),
        )
        if body.iloc[:30, best].notna().sum() >= 1:
            mapped.append((name, best))
            cursor = best + 1
        else:
            continue

    if not mapped:
        return df, notes

    frame = pd.DataFrame()
    for name, col in mapped:
        frame[name] = body.iloc[:, col].reset_index(drop=True)
    frame = frame.dropna(how="all").reset_index(drop=True)

    header_like = frame.apply(
        lambda row: len([v for v in row if not _is_missing(v)]) >= 3
        and all(isinstance(v, str) or _is_missing(v) for v in row),
        axis=1,
    )
    if header_like.any():
        frame = frame.loc[~header_like].reset_index(drop=True)

    stray_headers = frame.apply(
        lambda row: any(
            not _is_missing(v)
            and str(v).strip() in _AR_HEADER_MAP
            or (not _is_missing(v) and str(v).strip() in _AR_HEADER_MAP.values())
            for v in row
        ),
        axis=1,
    )
    if stray_headers.any():
        frame = frame.loc[~stray_headers].reset_index(drop=True)

    frame = frame.drop(
        columns=[c for c in frame.columns if frame[c].isna().all()]
    ).reset_index(drop=True)

    repeated = sum(
        1
        for _, row in body.iloc[: probe_n].iterrows()
        if sum(1 for v in row if not _is_missing(v)) >= 3
        and all(
            _cell_is_str(v) for v in row if not _is_missing(v)
        )
    )
    if repeated >= 2:
        notes.append(
            "repeated-block report: distinct sections in one sheet; "
            "extracted as a best-effort flat table"
        )

    if mapped:
        notes.append(
            f"report-table discovery: header row at row {best_idx + 1}, "
            f"{len(frame.columns)} columns, {len(frame)} data rows"
        )
    return frame, notes


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


_TOKEN_RE = re.compile(r"[^0-9a-z]+")

# Strong tokens are distinctive identifiers a category alone owns; weak tokens
# are shared, generic evidence (amount, id, date ...). Strong hits count 3x.
_STRONG_TOKENS = {
    "sales": {"sale", "transaction", "receipt", "basket", "register", "pos",
              "checkout", "invoice", "cash", "sold", "revenue", "net"},
    "inventory": {"hand", "batch", "lot", "expiry", "expiration", "stock",
                  "inventory", "writeoff", "reorder", "damage", "waste",
                  "balance"},
    "prescriptions": {"rx", "prescriber", "supply", "refill", "controlled",
                      "schedule", "ndc", "medication"},
    "patients": {"birth", "patient", "chronic", "adherence", "loyalty",
                 "demographic", "age", "gender"},
    "products": {"sku", "ndc", "generic", "brand", "strength", "therapeutic",
                 "dosage", "formulation", "shelf", "storage"},
    "suppliers": {"vendor", "supplier", "fill", "rate", "lead", "contract",
                  "distributor"},
    "purchase_orders": {"po", "ordered", "ordered_quantity", "delivery", "cost",
                        "unit_cost", "expected", "received", "bonus",
                        "returned", "vat"},
    "payers": {"payer", "claim", "rejection", "copay", "coinsurance", "plan",
               "deductible", "dir", "billed", "paid"},
}

_WEAK_TOKENS = {
    "sales": {"amount", "total", "payment", "price", "tax", "discount",
              "insurance", "ref", "devices", "number"},
    "inventory": {"quantity", "balance", "received", "warehouse", "location",
                  "cost", "unit"},
    "prescriptions": {"drug", "patient", "date", "filled", "pharmacy",
                      "quantity", "copy", "amount"},
    "patients": {"name", "phone", "email", "first", "last", "seen", "address",
                 "age"},
    "products": {"product", "name", "unit", "price", "cost", "manufacturer",
                 "description", "purchase"},
    "suppliers": {"rating", "order", "purchase", "address", "delivery", "time",
                  "terms"},
    "purchase_orders": {"order", "quantity", "amount", "supplier", "vendor",
                        "status"},
    "payers": {"amount", "reimbursement", "benefit", "claim", "member",
               "rejection", "code"},
}


def _singular_token(token: str) -> str:
    """Fold common English plurals so generic column generics agree."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _column_tokens(df: pd.DataFrame) -> set[str]:
    tokens: set[str] = set()
    for col in df.columns:
        for tok in _TOKEN_RE.split(str(col).lower()):
            tok = tok.strip("_")
            if tok:
                tokens.add(_singular_token(tok))
    return tokens


def detect_schema_category(df: pd.DataFrame) -> dict[str, float]:
    """
    Detect what type of data the dataframe contains based on column names.

    Column headers are tokenized (split on punctuation), normalized (lowercase,
    English plural folding), and scored against strong-distinctive and weak-
    generic token sets per category. Returns normalized confidence scores.

    Examples:
      sale_id/receipt_id/total_amount  -> mostly ``sales``
      rx_number/days_supply/prescriber -> ``prescriptions``
    """
    tokens = _column_tokens(df)

    scores = {
        category: 3 * len(tokens & _STRONG_TOKENS[category])
        for category in _STRONG_TOKENS
    }
    for category, weak in _WEAK_TOKENS.items():
        scores[category] += len(tokens & weak)

    total = sum(scores.values())
    if total > 0:
        scores = {category: value / total for category, value in scores.items()}
    return scores