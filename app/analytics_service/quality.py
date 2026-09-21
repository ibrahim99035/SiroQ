"""Data-quality checks run against a raw dataframe (FEATURE_CATALOG §2.1).

Checks implemented: header-confidence, duplicate rows, negative values,
type coercion, empty/null columns, unmapped columns. Each check reports a
status and detail; a 0-100 score aggregates the findings.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def check_duplicates(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
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


def check_negatives(df: pd.DataFrame) -> dict[str, Any]:
    neg = {}
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


def check_type_coercion(df: pd.DataFrame) -> dict[str, Any]:
    """Report values that look numeric/date-like but fail to parse."""
    issues: dict[str, int] = {}
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


def check_empty_or_null(df: pd.DataFrame) -> dict[str, Any]:
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


def check_unmapped_columns(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    mapped = {
        info.get("suggested_mapping")
        for info in field_scores.values()
        if info.get("suggested_mapping")
    }
    unmapped = [str(c) for c in df.columns if str(c) not in mapped]
    return {
        "check": "unmapped_columns",
        "status": "warn" if unmapped else "pass",
        "unmapped_columns": unmapped,
    }


def run_quality_checks(df: pd.DataFrame, field_scores: dict) -> dict[str, Any]:
    """Run the full check set and produce an aggregate 0-100 score."""
    checks = []
    if df is None or df.empty:
        checks.append({
            "check": "header_confidence",
            "status": "pass" if field_scores else "warn",
            "detail": "no columns to classify",
        })
        checks.append(check_empty_or_null(df))
        checks.append({"check": "duplicate_rows", "status": "pass", "count": 0})
        checks.append({"check": "negative_values", "status": "pass", "by_column": {}})
        checks.append({"check": "type_coercion", "status": "pass", "by_column": {}})
        checks.append({
            "check": "unmapped_columns",
            "status": "pass" if df is None else "warn",
            "unmapped_columns": [] if df is None else list(df.columns),
        })
        return {
            "score": 0,
            "checks": checks,
            "duplicate_count": 0,
            "findings_count": sum(1 for c in checks if c["status"] != "pass"),
        }

    low_conf = [
        field for field, info in field_scores.items()
        if info.get("status") == "unconfirmed" and info.get("suggested_mapping")
    ]
    checks.append({
        "check": "header_confidence",
        "status": "warn" if low_conf else "pass",
        "low_confidence_fields": low_conf,
    })
    checks.append(check_empty_or_null(df))
    checks.append(check_duplicates(df))
    checks.append(check_negatives(df))
    checks.append(check_type_coercion(df))
    checks.append(check_unmapped_columns(df, field_scores))

    # Scoring: 100 minus fixed penalties per triggered finding category.
    score = 100.0
    by_name = {c["check"]: c for c in checks}
    if by_name["empty_or_null"]["status"] != "pass":
        score -= 50
    if by_name["duplicate_rows"]["status"] == "fail":
        score -= 10
    if by_name["negative_values"]["status"] == "fail":
        score -= 10
    if by_name["type_coercion"]["status"] == "fail":
        score -= 10
    if by_name.get("unmapped_columns", {}).get("status") == "warn":
        score -= 5
    if by_name.get("header_confidence", {}).get("status") == "warn":
        score -= 5
    score = round(max(0.0, min(100.0, score)), 1)

    findings_count = sum(1 for c in checks if c["status"] != "pass")
    return {
        "score": score,
        "checks": checks,
        "duplicate_count": by_name["duplicate_rows"].get("count", 0),
        "findings_count": findings_count,
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