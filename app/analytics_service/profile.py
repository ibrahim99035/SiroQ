"""Column-level and file-level profiling of an ingested dataframe."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service.classification import detect_content


def _column_kind(series: pd.Series) -> tuple[str, float]:
    """Best-effort value-kind for a column (number/date/category/text)."""
    if series.empty:
        return "empty", 0.0
    dtype = series.dtype
    if pd.api.types.is_numeric_dtype(dtype):
        return "number", 88.0
    if isinstance(dtype, pd.DatetimeTZDtype) or dtype == np.dtype("datetime64[ns]"):
        return "date", 90.0
    if isinstance(dtype, pd.CategoricalDtype):
        return "category", 85.0
    kinds, score = detect_content(series.tolist())
    kind = "text"
    if "category" in kinds:
        kind = "category"
    elif "date" in kinds:
        kind = "date"
    elif "number" in kinds:
        kind = "number"
    return kind, score


def _profile_column(df: pd.DataFrame, name: str) -> dict[str, Any]:
    series = df[name]
    total = len(series)
    null_count = int(series.isna().sum()) if total else 0
    non_null = series.dropna()
    kind, kind_score = _column_kind(series)
    unique_count = int(non_null.nunique()) if len(non_null) else 0

    out: dict[str, Any] = {
        "name": name,
        "dtype": str(series.dtype),
        "kind": kind,
        "kind_score": round(kind_score, 1),
        "null_count": null_count,
        "null_pct": round(null_count / total * 100, 1) if total else 0.0,
        "unique_count": unique_count,
        "unique_pct": round(unique_count / len(non_null) * 100, 1) if len(non_null) else 0.0,
    }

    if kind == "number":
        num = pd.to_numeric(series, errors="coerce").dropna()
        if len(num):
            out["min"] = float(num.min())
            out["max"] = float(num.max())
            out["mean"] = float(num.mean())
            out["median"] = float(num.median())
            out["std"] = float(num.std()) if len(num) > 1 else 0.0
            out["sum"] = float(num.sum())
    elif kind == "date":
        dt = pd.to_datetime(series, errors="coerce").dropna()
        if len(dt):
            out["min"] = dt.min().isoformat()
            out["max"] = dt.max().isoformat()
            out["span_days"] = int((dt.max() - dt.min()).days)

    if kind in {"category", "text"} and len(non_null):
        vc = non_null.astype(str).value_counts(dropna=False)
        out["top_values"] = [
            {"value": k, "count": int(v)} for k, v in vc.head(10).items()
        ]

    out["sample_values"] = [str(v) for v in non_null.head(5).tolist()]
    return out


def profile_dataframe(df: pd.DataFrame) -> dict[str, Any]:
    """Statistical + structural profile of a dataframe."""
    columns = [str(c) for c in df.columns.tolist()]
    try:
        profile = {
            "row_count": int(len(df)),
            "column_count": len(columns),
            "columns": columns,
        }
        if df.empty:
            profile["memory_bytes"] = 0
            return profile
        profile["memory_bytes"] = int(df.memory_usage(deep=True).sum())
        profile["column_profiles"] = [
            _profile_column(df, c) for c in columns
        ]
    except Exception as exc:  # pragma: no cover - defensive
        profile = {
            "row_count": int(len(df)),
            "column_count": len(columns),
            "columns": columns,
            "profile_error": f"{type(exc).__name__}: {exc}",
        }
    return profile