"""Data-quality checks run against a raw dataframe (FEATURE_CATALOG §2.1).

Every check is a function decorated with ``@quality_check`` from
:mod:`app.analytics_service.registry`. The registry supplies the check order and
the score penalty, so adding a new check is a single decorated function — it is
then run automatically on both the normal and the empty-frame path.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service.registry import quality_check, quality_checks


@quality_check("header_confidence", penalty=5, needs_field_scores=True)
def check_header_confidence(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    column_count = 0 if df is None else len(df.columns)
    if column_count == 0:
        return {
            "check": "header_confidence",
            "status": "pass" if field_scores else "warn",
            "detail": "no columns to classify",
        }
    low_conf = [
        field for field, info in field_scores.items()
        if info.get("status") == "unconfirmed" and info.get("suggested_mapping")
    ]
    return {
        "check": "header_confidence",
        "status": "warn" if low_conf else "pass",
        "low_confidence_fields": low_conf,
    }


@quality_check("empty_or_null", penalty=50)
def check_empty_or_null(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    empty = df is None or len(df) == 0
    high_null = []
    if df is not None and not df.empty:
        for col in df.columns:
            pct = df[col].isna().mean()
            if pct > 0.5:
                high_null.append({"column": str(col), "null_pct": round(float(pct), 2)})
        if len(df) > 0 and any(isinstance(c, str) and c.startswith("Unnamed:")
                               for c in df.columns):
            high_null.append({"column": "(header-row detected)", "null_pct": 1.0})
    status = "fail" if empty or high_null else "pass"
    return {
        "check": "empty_or_null",
        "status": status,
        "empty_file": empty,
        "high_null_columns": high_null,
    }


@quality_check("duplicate_rows", penalty=10)
def check_duplicates(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    if df is None or df.empty:
        return {"check": "duplicate_rows", "status": "pass", "count": 0}
    mask = df.duplicated(keep="first")
    count = int(mask.sum())
    sample_rows = (
        df[mask].head(5).astype(str).to_dict("records") if count else []
    )
    return {
        "check": "duplicate_rows",
        "status": "fail" if count else "pass",
        "count": count,
        "sample_rows": sample_rows,
    }


@quality_check("negative_values", penalty=10)
def check_negatives(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    neg = {}
    if df is not None:
        for col in df.columns:
            series = df[col]
            if not pd.api.types.is_numeric_dtype(series) and not isinstance(
                series.dtype, pd.CategoricalDtype
            ):
                coerced = pd.to_numeric(series, errors="coerce")
            else:
                coerced = series
            n = int((coerced < 0).sum())
            if n:
                neg[str(col)] = n
    return {
        "check": "negative_values",
        "status": "fail" if neg else "pass",
        "by_column": neg,
    }


@quality_check("type_coercion", penalty=10)
def check_type_coercion(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    """Report values that look numeric/date-like but fail to parse."""
    issues: dict[str, int] = {}
    if df is not None:
        for col in df.columns:
            series = df[col]
            non_null = series.dropna()
            if len(non_null) < 3:
                continue
            as_str = non_null.astype(str).str.strip()
            # skip clearly-text columns (low numeric ratio)
            numerish = as_str.str.replace(r"[,%£$ €]", "", regex=True)
            num_ratio = numerish.str.match(r"^-?\d+(\.\d+)?$").mean()
            if num_ratio < 0.5:
                continue
            parsed = pd.to_numeric(numerish, errors="coerce")
            failed = int(parsed.isna().sum())
            if failed:
                issues[str(col)] = failed
    return {
        "check": "type_coercion",
        "status": "fail" if issues else "pass",
        "by_column": issues,
    }


@quality_check("unmapped_columns", penalty=5, needs_field_scores=True)
def check_unmapped_columns(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    mapped = {
        info.get("suggested_mapping")
        for info in field_scores.values()
        if info.get("suggested_mapping")
    }
    if df is None:
        return {
            "check": "unmapped_columns",
            "status": "pass",
            "unmapped_columns": [],
        }
    if df.empty:
        return {
            "check": "unmapped_columns",
            "status": "warn",
            "unmapped_columns": [str(c) for c in df.columns],
        }
    unmapped = [str(c) for c in df.columns if str(c) not in mapped]
    return {
        "check": "unmapped_columns",
        "status": "warn" if unmapped else "pass",
        "unmapped_columns": unmapped,
    }


def _run_checks(df: pd.DataFrame, field_scores: dict) -> list[dict[str, Any]]:
    """Execute every registered check in registration order.

    Checks declared with ``needs_field_scores=True`` receive the classification
    field map; the rest always run. A single failing check never aborts the set.
    """
    out = []
    for entry in quality_checks.all():
        try:
            if entry.meta.get("needs_field_scores"):
                result = entry.fn(df, field_scores or {})
            else:
                result = entry.fn(df, None)
        except Exception as exc:  # defensive: one check must never break the set
            result = {
                "check": entry.name,
                "status": "warn",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        out.append(result)
    return out


def _score(checks: list[dict[str, Any]]) -> float:
    """Aggregate the per-check penalties into a 0-100 score (100 - penalties)."""
    by_name = {}
    for entry in quality_checks.all():
        by_name.setdefault(entry.name, entry)
    score = 100.0
    for check in checks:
        verdict = check.get("status")
        if verdict == "pass":
            continue
        entry = by_name.get(check.get("check"))
        score -= entry.meta.get("penalty", 0.0) if entry else 0.0
    return round(max(0.0, min(100.0, score)), 1)


def run_quality_checks(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    """Run the full registered check set and produce an aggregate 0-100 score."""
    checks = _run_checks(df, field_scores)
    duplicate_count = 0
    for check in checks:
        if check.get("check") == "duplicate_rows":
            duplicate_count = check.get("count", 0)
    # An unreadable/empty frame still runs the checks (which stay self-passing)
    # but is scored 0 outright — matching the historical contract.
    score = 0.0 if df is None or getattr(df, "empty", True) else _score(checks)
    return {
        "score": score,
        "checks": checks,
        "duplicate_count": duplicate_count,
        "findings_count": sum(1 for c in checks if c.get("status") != "pass"),
    }


def findings_detail(quality: dict[str, Any]) -> list[str]:
    """Human-readable list of the non-passing findings for a file."""
    out = []
    for c in quality.get("checks", []):
        if c.get("status") == "pass":
            continue
        detail = c.get("detail") or c.get("count") or c.get("by_column") or ""
        out.append(f"{c['check']}={c['status']} {detail}")
    return out