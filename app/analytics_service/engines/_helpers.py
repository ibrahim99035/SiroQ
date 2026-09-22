"""Shared helpers for domain-analytics engines.

Every engine lives in its own module under :mod:`app.analytics_service.engines`
and is wired into the dispatch table with the ``@domain_engine`` decorator from
:mod:`app.analytics_service.registry`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def pick_col(df: pd.DataFrame, fmap: dict, canonical: str, *candidates: str):
    """Resolve a source column for a canonical field, then fall back to
    candidate column names seen directly on the dataframe."""
    from_name = fmap.get(canonical)
    if from_name and from_name in df.columns:
        return from_name
    for name in candidates:
        if name in df.columns:
            return name
    return None


def numeric(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def safe_sum(s: pd.Series) -> float:
    return float(pd.to_numeric(s, errors="coerce").sum())


def col_sum(df: pd.DataFrame, col: str) -> float:
    return float(numeric(df, col).sum())


def categorical_counts(df: pd.DataFrame, cols: list[str], limit: int = 10) -> dict:
    out = {}
    for col in cols:
        if col in df.columns:
            vc = df[col].astype(str).value_counts().head(limit)
            out[col] = [{"value": k, "count": int(v)} for k, v in vc.items()]
    return out