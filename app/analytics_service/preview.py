"""Live data-source introspection for the dashboard.

Re-parses a stored raw file from disk (without re-running the analysis
pipeline) so the user can see the actual rows/values and build a forecastable
time series from whatever columns they choose. Pure pandas, zero new deps.
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

import pandas as pd

from app.analytics_service.ingestion import IngestedFile, read_file
from app.analytics_service.storage import read_bytes

# Column-name hints used to auto-pick a date column and a value column.
_DATE_HINTS = ("date", "time", "ts", "timestamp", "datetime", "created", "day", "حدث")
_AMOUNT_HINTS = (
    "amount", "revenue", "total", "price", "value", "qty", "quantity",
    "cost", "margin", "net", "sales", "sum",
)


def load_file(stored) -> IngestedFile:
    """Re-parse a stored file from disk into an ingested dataframe."""
    return read_file(read_bytes(stored.stored_path), stored.original_filename)


def select_frame(ing: IngestedFile, sheet: str | None = None) -> tuple[pd.DataFrame, str]:
    """Pick the requested sheet (by name or 0-based index) or the first one."""
    if not ing.sheets:
        return pd.DataFrame(), ""
    if sheet is None or sheet == "default":
        return ing.dataframe, next(iter(ing.sheets))
    if sheet in ing.sheets:
        return ing.sheets[sheet], sheet
    if sheet.isdigit():
        names = list(ing.sheets)
        idx = int(sheet)
        if 0 <= idx < len(names):
            return ing.sheets[names[idx]], names[idx]
    # unknown sheet name/index -> fall back to the first sheet
    return ing.dataframe, next(iter(ing.sheets))


def _column_kind(dtype: str, series: pd.Series) -> str:
    if "datetime" in dtype or "date" in dtype:
        return "date"
    if dtype.startswith(("int", "float")):
        return "number"
    probe = series.dropna().astype(str).head(20)
    if len(probe):
        first = probe.iloc[0]
        if first.startswith(("202", "19")) and "/" in first or "-" in first:
            parsed = pd.to_datetime(probe, errors="coerce")
            if parsed.notna().mean() >= 0.9:
                return "date"
    return "text"


def infer_columns(df: pd.DataFrame, sample_size: int = 5) -> list[dict[str, Any]]:
    """Per-column metadata for the pickers and the preview table."""
    columns = []
    for col in df.columns:
        s = df[col]
        kind = _column_kind(str(s.dtype), s)
        if kind == "number":
            non_null = pd.to_numeric(s, errors="coerce").dropna()
        else:
            non_null = s.dropna()
        samples = [str(v) for v in non_null.head(sample_size).to_list()]
        columns.append({
            "name": str(col),
            "dtype": str(s.dtype),
            "kind": kind,
            "null_count": int(s.isna().sum()),
            "sample_values": samples,
        })
    return columns


def pick_date_column(df: pd.DataFrame, preferred: str | None = None) -> str | None:
    if preferred and preferred in df.columns:
        return preferred
    for col in df.columns:
        dtype = str(df[col].dtype)
        if "datetime" in dtype or "date" in dtype:
            return col
    for col in df.columns:
        low = str(col).lower()
        if any(h in low for h in _DATE_HINTS):
            if _column_kind(str(df[col].dtype), df[col]) == "date":
                return col
    for col in df.columns:
        if _column_kind(str(df[col].dtype), df[col]) == "date":
            return col
    return None


def pick_value_column(df: pd.DataFrame, preferred: str | None = None) -> str | None:
    if preferred and preferred in df.columns:
        return preferred if str(df[preferred].dtype).startswith(("int", "float")) else None
    for col in df.columns:
        low = str(col).lower()
        if str(df[col].dtype).startswith(("int", "float")) and any(h in low for h in _AMOUNT_HINTS):
            return col
    for col in df.columns:
        if str(df[col].dtype).startswith(("int", "float")):
            return col
    return None


_MAX_REINDEX_DAYS = 730  # beyond this, leave gaps as-is instead of dense fill


def build_series(
    df: pd.DataFrame,
    date_column: str,
    value_column: str | None = None,
    agg: str = "sum",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Aggregate a dataframe into a daily series ``[{date, value}]``.

    ``agg`` is one of ``sum`` / ``mean`` / ``count``. With ``value_column``
    missing, ``agg`` is forced to ``count`` (rows per day).
    """
    if df.empty or date_column not in df.columns:
        return [], {"points": 0, "date_column": date_column, "value_column": value_column, "agg": agg}
    agg = agg if agg in ("sum", "mean", "count") else "sum"
    ts = pd.to_datetime(df[date_column], errors="coerce")
    frame = df.assign(_day=ts.dt.date)
    frame = frame[frame["_day"].notna()]
    if frame.empty:
        return [], {"points": 0, "date_column": date_column, "value_column": value_column, "agg": agg}

    if value_column and value_column in frame.columns:
        num = pd.to_numeric(frame[value_column], errors="coerce")
        frame = frame.assign(_val=num).dropna(subset=["_val"])
        if agg == "count":
            grouped = frame.groupby("_day")["_val"].count()
        elif agg == "mean":
            grouped = frame.groupby("_day")["_val"].mean()
        else:
            grouped = frame.groupby("_day")["_val"].sum()
    else:
        value_column = None
        agg = "count"
        grouped = frame.groupby("_day").size()

    grouped = grouped.sort_index()

    min_day, max_day = grouped.index.min(), grouped.index.max()
    span_days = (max_day - min_day).days if min_day is not None else 0
    reindexed = False
    if min_day is not None and span_days <= _MAX_REINDEX_DAYS:
        full = pd.date_range(min_day, max_day, freq="D").date
        grouped = grouped.reindex(full, fill_value=0.0)
        reindexed = True

    rows = [{"date": d.isoformat(), "value": round(float(v), 4)} for d, v in grouped.items()]
    meta = {
        "points": len(rows),
        "date_column": date_column,
        "value_column": value_column,
        "agg": agg,
        "min_date": min_day.isoformat() if min_day is not None else None,
        "max_date": max_day.isoformat() if max_day is not None else None,
        "span_days": span_days,
        "reindexed": reindexed,
    }
    return rows, meta


def preview(stored, sheet: str | None = None, rows: int = 5, offset: int = 0) -> dict[str, Any]:
    """Full preview payload for one stored file (and optional sheet)."""
    ing = load_file(stored)
    df, sheet_label = select_frame(ing, sheet)
    sheets = list(ing.sheets)
    row_count = int(len(df))
    if not df.empty:
        data = df.reset_index(drop=True).iloc[offset:offset + rows]
        sample_rows = data.where(data.notna(), None).to_dict("records")
    else:
        sample_rows = []
    payload: dict[str, Any] = {
        "file_id": stored.id,
        "file_type": ing.file_type,
        "filename": stored.original_filename,
        "size_bytes": stored.size_bytes,
        "sheet": sheet_label,
        "sheets": sheets,
        "row_count": row_count,
        "offset": offset,
        "columns": infer_columns(df) if not df.empty else [],
        "sample_rows": sample_rows,
        "recommended": {},
        "errors": ing.errors,
        "notes": ing.notes,
    }
    if not df.empty:
        payload["recommended"] = {
            "date_col": pick_date_column(df),
            "value_col": pick_value_column(df),
        }
    return payload