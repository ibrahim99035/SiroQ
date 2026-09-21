"""File ingestion and schema detection for the analytical service."""
from __future__ import annotations

import io
import json
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


def detect_schema_category(df: pd.DataFrame) -> dict[str, float]:
    """
    Detect what type of data the dataframe contains based on column names.
    Returns a confidence score for each category.
    """
    columns = [str(c).lower() for c in df.columns]

    def _keyword_hits(keywords: set) -> int:
        return sum(1 for kw in keywords if any(kw in col for col in columns))

    scores = {
        "sales": _keyword_hits({"sale", "transaction", "receipt", "revenue", "amount",
                                "payment", "cash", "insurance", "total", "basket",
                                "pos", "register"}),
        "inventory": _keyword_hits({"inventory", "stock", "batch", "lot", "expiry",
                                    "expiration", "quantity", "on_hand", "received",
                                    "writeoff", "waste", "damage"}),
        "prescriptions": _keyword_hits({"prescription", "rx", "refill", "days_supply",
                                        "prescriber", "patient", "ndc", "controlled",
                                        "schedule"}),
        "patients": _keyword_hits({"patient", "age", "gender", "chronic", "adherence",
                                   "loyalty", "first_seen", "last_seen", "demographic"}),
        "products": _keyword_hits({"product", "drug", "medication", "generic", "brand",
                                   "form", "strength", "unit", "category",
                                   "therapeutic", "cost_per_unit", "selling_price",
                                   "reimbursement", "shelf_life", "storage"}),
        "suppliers": _keyword_hits({"supplier", "vendor", "purchase", "order", "po",
                                    "lead_time", "fill_rate", "delivery", "340b",
                                    "contract"}),
        "purchase_orders": _keyword_hits({"po", "purchase_order", "order", "vendor",
                                          "supplier", "delivery", "lead_time",
                                          "contract"}),
        "payers": _keyword_hits({"payer", "plan", "claim", "rejection", "paid",
                                 "billed", "contract_rate", "dir_fee", "copay",
                                 "coinsurance"}),
    }

    # Bias towards the most specific matches: a keyword that shows up in a column
    # name is a weak signal, but one that is the full column name is a strong one.
    for name, keywords in {
        "sales": {"transaction_ref", "sale_id", "sale_timestamp", "receipt_id",
                  "pos_id", "total_amount"},
        "inventory": {"batch_id", "quantity_on_hand", "lot_number", "expiry_date",
                      "expiration_date"},
        "prescriptions": {"rx_number", "days_supply", "prescriber_id", "ndc"},
        "patients": {"patient_id", "patient_hash", "date_of_birth", "adherence_rate"},
        "products": {"product_id", "ndc", "sku", "raw_name", "selling_price",
                     "cost_per_unit"},
        "suppliers": {"supplier_id", "vendor_id", "contract_number"},
        "purchase_orders": {"po_number", "purchase_order_id", "ordered_quantity"},
        "payers": {"payer_id", "plan_id", "rejection_code"},
    }.items():
        scores[name] += _keyword_hits(keywords)

    # Normalize scores
    total = sum(scores.values())
    if total > 0:
        scores = {k: v / total for k, v in scores.items()}

    return scores


def infer_primary_keys(df: pd.DataFrame, category: str) -> list[str]:
    """Infer likely primary key columns based on category and column names."""
    columns = set(df.columns)

    key_patterns = {
        "sales": ["transaction_id", "sale_id", "receipt_id", "transaction_ref", "id"],
        "inventory": ["batch_id", "lot_number", "inventory_id", "id"],
        "prescriptions": ["rx_number", "prescription_id", "rx_id", "id"],
        "patients": ["patient_id", "patient_hash", "id"],
        "products": ["product_id", "ndc", "sku", "id"],
        "suppliers": ["supplier_id", "vendor_id", "id"],
        "purchase_orders": ["po_number", "purchase_order_id", "id"],
        "payers": ["payer_id", "plan_id", "id"],
    }

    candidates = key_patterns.get(category, ["id"])
    return [c for c in candidates if c in columns]


def validate_required_columns(df: pd.DataFrame, category: str) -> tuple[bool, list[str]]:
    """Validate that required columns for a category are present."""
    required = {
        "sales": ["association_id", "pharmacy_id", "sale_timestamp", "total_amount"],
        "inventory": ["association_id", "pharmacy_id", "batch_id", "quantity_on_hand"],
        "prescriptions": ["association_id", "patient_id", "product_id", "rx_number", "days_supply"],
        "patients": ["association_id", "patient_hash"],
        "products": ["association_id", "raw_name"],
        "suppliers": ["association_id", "supplier_name"],
        "purchase_orders": ["association_id", "supplier_id", "product_id", "ordered_quantity"],
        "payers": ["association_id", "payer_name"],
    }

    required_cols = required.get(category, [])
    missing = [c for c in required_cols if c not in df.columns]
    return len(missing) == 0, missing


def merge_sheets(ingested: IngestedFile, category: str) -> pd.DataFrame:
    """Merge multiple sheets if they appear to be the same category."""
    if len(ingested.sheets) <= 1:
        return ingested.dataframe

    # For now, just return the first sheet
    # In production, could merge sheets with same schema
    return ingested.dataframe