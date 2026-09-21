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

    try:
        if file_type == "csv":
            df = pd.read_csv(io.BytesIO(content))
            sheets = {"default": df}
        elif file_type == "excel":
            excel_file = pd.read_excel(io.BytesIO(content), sheet_name=None)
            sheets = excel_file
            df = list(excel_file.values())[0] if excel_file else pd.DataFrame()
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
    )


_TOKEN_RE = re.compile(r"[^0-9a-z]+")

# Strong tokens are distinctive identifiers a category alone owns; weak tokens
# are shared, generic evidence (amount, id, date ...). Strong hits count 3x.
_STRONG_TOKENS = {
    "sales": {"sale", "transaction", "receipt", "basket", "register", "pos",
              "checkout", "invoice", "cash"},
    "inventory": {"hand", "batch", "lot", "expiry", "expiration", "stock",
                  "inventory", "writeoff", "reorder", "damage", "waste"},
    "prescriptions": {"rx", "prescriber", "supply", "refill", "controlled",
                      "schedule", "ndc", "medication"},
    "patients": {"birth", "patient", "chronic", "adherence", "loyalty",
                 "demographic", "age", "gender"},
    "products": {"sku", "ndc", "generic", "brand", "strength", "therapeutic",
                 "dosage", "formulation", "shelf", "storage"},
    "suppliers": {"vendor", "supplier", "fill", "rate", "lead", "contract",
                  "distributor"},
    "purchase_orders": {"po", "ordered", "ordered_quantity", "delivery", "cost",
                        "unit_cost", "expected", "received"},
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