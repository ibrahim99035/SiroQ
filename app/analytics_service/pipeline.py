"""Orchestrates a deep-analysis run over an application's stored files and
produces the JSON-safe report + summary document that gets persisted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service import analytics, classification, ingestion, profile, quality
from app.analytics_service.storage import read_bytes

ENGINE_VERSION = "0.1.0"


def sanitize(obj: Any) -> Any:
    """Recursively convert numpy/pandas scalars and NaN into JSON-safe values."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (float, int)):
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj
    if isinstance(obj, (np.datetime64,)):
        return pd.Timestamp(obj).isoformat()
    if hasattr(obj, "isoformat"):  # datetime / date / Timestamp
        try:
            return obj.isoformat()
        except TypeError:  # pragma: no cover
            return None
    return obj


def _analyze_file(stored_file) -> dict[str, Any]:
    """Analyze a single stored file into its report section."""
    section: dict[str, Any] = {
        "file_id": str(stored_file.id),
        "filename": stored_file.original_filename,
        "file_type": stored_file.file_type,
        "sha256": stored_file.sha256,
        "size_bytes": stored_file.size_bytes,
    }

    content = read_bytes(stored_file.stored_path)
    try:
        ingested = ingestion.read_file(content, stored_file.original_filename)
    except Exception as exc:  # unreachable via read_file but defensive
        ingested = None
        section["errors"] = [f"{type(exc).__name__}: {exc}"]

    df = ingested.dataframe if ingested is not None else None
    section["row_count"] = 0
    section["columns"] = []
    if df is None or df.empty:
        section["profile"] = profile.profile_dataframe(df) if df is not None else {
            "row_count": 0, "column_count": 0, "columns": [], "profile_error": "no data"}
        section["classification"] = None
        if ingested is not None:
            section["errors"] = list(ingested.errors) or ["file contained no data"]
        section["data_quality"] = quality.run_quality_checks(df, {})
        section["domain_analytics"] = {"skipped": "file unreadable or empty"}
        return section

    section["row_count"] = int(len(df))
    section["columns"] = list(df.columns)

    section["profile"] = profile.profile_dataframe(df)
    classified = classification.classify_dataframe(df)
    section["classification"] = classified

    fmap = {
        field: info.get("suggested_mapping")
        for field, info in classified["field_scores"].items()
        if info.get("suggested_mapping")
    }
    qc = quality.run_quality_checks(df, classified["field_scores"])
    section["data_quality"] = qc
    section["quality_findings"] = quality.findings_detail(qc)

    cats = ingestion.detect_schema_category(df)
    top_category = max(cats, key=cats.get) if cats and max(cats.values()) > 0 else None
    section["categories"] = {k: round(v, 3) for k, v in cats.items()}
    section["top_category"] = top_category
    section["domain_analytics"] = (
        analytics.run_domain_analytics(df, top_category, fmap)
        if top_category else {"skipped": "no category detected"}
    )
    return section


def analyze_application(application, stored_files: list) -> tuple[dict, dict]:
    """Run the full pipeline. Returns ``(summary, report)`` both JSON-safe."""
    file_sections = [_analyze_file(f) for f in stored_files]

    categories_detected: dict[str, list[str]] = {}
    total_rows = 0
    scores = []
    for sec in file_sections:
        total_rows += sec.get("row_count", 0)
        if sec.get("top_category"):
            categories_detected.setdefault(sec["top_category"], []).append(sec["filename"])
        if sec.get("data_quality"):
            scores.append(sec["data_quality"].get("score", 0))

    findings_count = sum(
        sec.get("data_quality", {}).get("findings_count", 0)
        for sec in file_sections
    ) + sum(len(sec.get("errors", [])) for sec in file_sections)

    summary = {
        "file_count": len(file_sections),
        "total_rows": total_rows,
        "categories_detected": categories_detected,
        "data_quality_score": (
            round(sum(scores) / len(scores), 1) if scores else None
        ),
        "findings_count": findings_count,
    }

    report = {
        "application_id": str(application.id),
        "application_name": application.name,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
        "files": file_sections,
    }
    return sanitize(summary), sanitize(report)