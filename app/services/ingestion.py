"""Bronze persistence, row validation and the Silver commit step of ingestion.

Validation is plain Python and covers the three Phase-1 checks:
  * negative quantity / unit price / total amount
  * exact duplicate row within the dataset (hash-based)
  * orphan product reference on a sale line (empty / unmatchable product)
Valid rows still commit when siblings are rejected (partial success).

Phase 2 additions:
  * zip archive extraction (bulk upload)
  * multi-sheet Excel auto-splitting
"""
import hashlib
import os
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd


def save_bronze(content: bytes, bronze_root: str, association_id: str,
                dataset_id: str, original_filename: str) -> str:
    directory = os.path.join(bronze_root, str(association_id), str(dataset_id))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, original_filename)
    with open(path, "wb") as f:
        f.write(content)
    return path


def read_frame(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path)
    if path.lower().endswith((".xlsx", ".xls")):
        df = pd.read_excel(path)
        if any(str(c).startswith("Unnamed:") for c in df.columns):
            df = pd.read_excel(path, header=1)
        return df
    raise ValueError(f"Unsupported file type: {path}")


def _row_hash(mapping, row) -> str:
    h = hashlib.sha256()
    for field, col in sorted(mapping.items()):
        if col is None:
            continue
        h.update(f"{field}={row.get(col)}|".encode("utf-8", "replace"))
    return h.hexdigest()


def _number(value):
    if value is None:
        return None
    try:
        f = float(value)
        return f
    except (TypeError, ValueError):
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return None


def validate_rows(df, mapping, canonical_columns=("quantity", "unit_price", "total_amount")):
    """Return (valid_indices, errors) where each error is {row, reasons[]}."""
    seen = set()
    valid = []
    errors = []
    required_number = [c for c in canonical_columns if mapping.get(c)]
    for i, (idx, row) in enumerate(df.iterrows()):
        reasons = []
        # 1) negative numeric check
        for field in required_number:
            val = _number(row.get(mapping[field]))
            if val is not None and val < 0:
                reasons.append(f"negative {field} ({val})")
        # 2) exact duplicate within dataset
        h = _row_hash(mapping, row)
        if h in seen:
            reasons.append("duplicate row (identical values)")
        seen.add(h)
        # 3) orphan product reference on a sale line
        if mapping.get("product_name"):
            pval = row.get(mapping["product_name"])
            if pval is None or (isinstance(pval, str) and not pval.strip()):
                reasons.append("orphan product reference (no product value)")
            elif pd.isna(pval):
                reasons.append("orphan product reference (no product value)")
        if reasons:
            errors.append({"row": idx, "reasons": reasons})
        else:
            valid.append(i)
    return valid, errors


def commit_dataset(db, association_id, application_id, dataset_id, df,
                   mapping, pharmacy_id, uploaded_by_id):
    """Insert rows from the mapped file into the Silver schema.

    Returns (committed, errors, products_created).
    """
    from app.models.models import Sales, SaleLines, Products, Datasets

    valid, errors = validate_rows(df, mapping)
    product_cache = {}
    now = datetime.now(timezone.utc)
    committed = 0

    for i in valid:
        row = df.iloc[i]
        row_rec = {c: row.get(mapping[c]) for c in mapping if mapping.get(c)}

        product_id = None
        if mapping.get("product_name"):
            pname = str(row.get(mapping["product_name"])).strip()
            pid = product_cache.get(pname)
            if pid is None:
                existing = db.query(Products).filter(
                    Products.association_id == association_id,
                    Products.raw_name == pname,
                ).first()
                if existing:
                    pid = existing.id
                else:
                    prod = Products(id=str(uuid.uuid4()), association_id=association_id,
                                    raw_name=pname, canonical_name=pname)
                    db.add(prod)
                    db.flush()
                    # RLS is satisfied because the request already SET the tenant context
                    pid = prod.id
                product_cache[pname] = pid
            product_id = pid

        sale = Sales(
            id=str(uuid.uuid4()),
            association_id=association_id,
            pharmacy_id=pharmacy_id,
            application_id=application_id,
            sale_timestamp=_row_ts(row_rec.get("sale_timestamp"), now),
            payment_method=_row_text(row_rec.get("payment_method")),
            total_amount=_number(row_rec.get("total_amount")) or 0,
            currency="USD",
            extra_attributes={},
        )
        db.add(sale)
        db.flush()

        if product_id is not None:
            line = SaleLines(
                id=str(uuid.uuid4()),
                association_id=association_id,
                sale_id=sale.id,
                product_id=product_id,
                quantity=_number(row_rec.get("quantity")) or 0,
                unit_price=_number(row_rec.get("unit_price")) or 0,
            )
            db.add(line)
        committed += 1

    db.flush()
    return committed, errors, len(product_cache)


def _row_ts(value, default):
    if value is None:
        return default
    try:
        df = pd.to_datetime(value)
        if pd.isna(df):
            return default
        return df.to_pydatetime()
    except (TypeError, ValueError):
        return default


def _row_text(value):
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def extract_zip(file_content: bytes) -> list[tuple[str, bytes]]:
    """Extract all files from a zip archive. Returns list of (filename, content)."""
    files = []
    with zipfile.ZipFile(BytesIO(file_content), 'r') as zf:
        for name in zf.namelist():
            if not name.endswith('/'):  # skip directories
                with zf.open(name) as f:
                    files.append((name, f.read()))
    return files


def get_excel_sheets(file_path: str) -> list[str]:
    """Get all sheet names from an Excel file."""
    import openpyxl
    wb = openpyxl.load_workbook(file_path, read_only=True)
    return wb.sheetnames


def read_excel_sheet(file_path: str, sheet_name: str) -> pd.DataFrame:
    """Read a specific sheet from an Excel file."""
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    if any(str(c).startswith("Unnamed:") for c in df.columns):
        df = pd.read_excel(file_path, sheet_name=sheet_name, header=1)
    return df