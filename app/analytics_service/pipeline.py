"""Orchestrates a deep-analysis run over an application's stored files and
produces the JSON-safe report + summary document that gets persisted.
"""
from __future__ import annotations

from datetime import date as dt_date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service import (
    analytics,
    classification,
    forecast as forecast_engine,
    ingestion,
    insights,
    profile,
    quality,
)
from app.analytics_service.registry import file_analyzer, file_analyzers
from app.analytics_service.storage import read_bytes

ENGINE_VERSION = "0.1.0"

# How far to project, and the shortest series worth projecting. The engine fits
# every registered model and scores them on a holdout slice, so a handful of
# points produces a confident-looking curve from noise. Below the floor we emit
# no forecast rather than a flattering one.
CLIENT_FORECAST_HORIZON = 6
CLIENT_FORECAST_MIN_POINTS = 6


def _num(value: Any) -> bool:
    """True when `value` is a finite real number."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


@file_analyzer("profile", order=10)
def _stage_profile(df, ctx):
    ctx["profile"] = profile.profile_dataframe(df)


@file_analyzer("classification", order=20)
def _stage_classification(df, ctx):
    classified = classification.classify_dataframe(df)
    ctx["classification"] = classified
    ctx["field_scores"] = classified["field_scores"]
    ctx["fmap"] = {
        field: info.get("suggested_mapping")
        for field, info in classified["field_scores"].items()
        if info.get("suggested_mapping")
    }


@file_analyzer("quality", order=30)
def _stage_quality(df, ctx):
    qc = quality.run_quality_checks(df, ctx["field_scores"])
    ctx["data_quality"] = qc
    ctx["quality_findings"] = quality.findings_detail(qc)


@file_analyzer("rows", order=35)
def _stage_rows(df, ctx):
    """Drop structural report rows before anything aggregates money.

    Quality and profiling deliberately still see every row -- a blank spacer or
    a repeated invoice header is a fact about the file worth scoring. Only the
    money-touching stages get the transactional view.
    """
    kept, stats = ingestion.transactional_rows(df)
    ctx["transactional_rows"] = stats
    ctx["analysis_df"] = kept


@file_analyzer("domain", order=40)
def _stage_domain(df, ctx):
    frame = ctx.get("analysis_df", df)
    cats = ingestion.detect_schema_category(frame)
    top_category = max(cats, key=cats.get) if cats and max(cats.values()) > 0 else None
    ctx["categories"] = {k: round(v, 3) for k, v in cats.items()}
    ctx["top_category"] = top_category
    ctx["domain_analytics"] = (
        analytics.run_domain_analytics(frame, top_category, ctx["fmap"])
        if top_category
        else {"skipped": "no category detected"}
    )


@file_analyzer("insights", order=50)
def _stage_insights(df, ctx):
    """Derived metrics (margin, concentration, waste, trends).

    Runs after ``domain`` so the detected category can be attached. Rules
    degrade individually, so a file with no date column still yields every
    non-trend insight.
    """
    ctx["insights"] = insights.compute_insights(
        ctx.get("analysis_df", df), ctx.get("fmap"), ctx.get("top_category")
    )


def _trend_series(ctx) -> tuple[str, list, list] | None:
    """Pull the primary time series out of the trend insights, if any ran.

    The trend rules already resample revenue into a dated series as part of
    computing the growth numbers, so the series is there in ``evidence``. Reading
    it back is cheaper and more consistent than re-deriving it: the forecast then
    forecasts exactly the series the report quotes in its trend insight.

    Revenue wins over volume when both are present -- a forecast of sales is the
    decision a reviewer acts on, and volume is a secondary read on the same rows.
    """
    for key in ("revenue_trend", "volume_trend"):
        for item in ctx.get("insights") or []:
            if item.get("key") != key or item.get("status") != "ok":
                continue
            series = (item.get("evidence") or {}).get("series") or []
            dates = [str(p.get("period")) for p in series if p.get("period")]
            values = [p.get("value") for p in series]
            # `_trend` refuses to report on fewer than 3 periods, so anything
            # reaching here is already long enough to have a shape.
            if len(values) >= 3 and len(dates) == len(values):
                label = "Revenue" if key == "revenue_trend" else "Units sold"
                return label, dates, [float(v) for v in values if _num(v)]
    return None


@file_analyzer("forecast", order=60)
def _stage_forecast(df, ctx):
    """Statistically project the file's primary time series.

    Runs after ``insights`` so it can reuse the series they already built.
    Deliberately one series per file: the point is a forward read on the
    headline number, not a forecast of every column. A file with no usable
    series gets no forecast node at all rather than a flat, meaningless one.
    """
    picked = _trend_series(ctx)
    if not picked:
        ctx["forecast"] = None
        return
    label, dates, values = picked
    # The engine caps the history it keeps at 24 points, so a two-year file and a
    # six-week file both arrive here with the same series length. The floor is
    # about how much shape there is to project, so it counts what we pass in.
    if len(values) < CLIENT_FORECAST_MIN_POINTS:
        ctx["forecast"] = None
        return
    result = forecast_engine.forecast(values, dates=dates, horizon=CLIENT_FORECAST_HORIZON)
    result["series_label"] = label
    result["granularity"] = _granularity_of(ctx, dates)
    ctx["forecast"] = result


def _granularity_of(ctx, dates: list) -> str:
    """How far apart the observations are, as a word the client can label with."""
    granularity = None
    for item in ctx.get("insights") or []:
        if item.get("key") in ("revenue_trend", "volume_trend") and item.get("status") == "ok":
            granularity = (item.get("evidence") or {}).get("granularity")
            break
    if granularity:
        return str(granularity)
    try:
        return {1: "day", 7: "week", 30: "month"}.get(
            (dt_date.fromisoformat(dates[-1]) - dt_date.fromisoformat(dates[0])).days
            // max(1, len(dates) - 1),
            "period",
        )
    except (ValueError, IndexError):
        return "period"


def _run_stages(df, ctx) -> None:
    """Execute every registered analysis stage in order on the shared ctx."""
    for entry in file_analyzers.all():
        try:
            entry.fn(df, ctx)
        except Exception as exc:  # defensive: one stage never breaks the run
            ctx.setdefault("stage_errors", []).append(
                f"{entry.name}: {type(exc).__name__}: {exc}"
            )


def _analyze_dataframe(df, *, sheet=None) -> dict[str, Any]:
    """Run profile / classification / quality / domain analytics over one frame."""
    sub: dict[str, Any] = {"sheet": sheet} if sheet is not None else {}

    if df is None or df.empty:
        sub["row_count"] = 0
        sub["columns"] = []
        sub["profile"] = (
            profile.profile_dataframe(df)
            if df is not None
            else {
                "row_count": 0,
                "column_count": 0,
                "columns": [],
                "profile_error": "no data",
            }
        )
        sub["classification"] = None
        sub["data_quality"] = quality.run_quality_checks(df, {})
        sub["domain_analytics"] = {"skipped": "sheet unreadable or empty"}
        sub["insights"] = []
        sub["errors"] = ["sheet unreadable or empty"] if df is None else []
        sub["top_category"] = None
        sub["forecast"] = None
        return sub

    ctx: dict[str, Any] = {}
    _run_stages(df, ctx)

    sub["row_count"] = int(len(df))
    sub["columns"] = list(df.columns)
    sub["profile"] = ctx["profile"]
    sub["classification"] = ctx["classification"]
    sub["data_quality"] = ctx["data_quality"]
    sub["quality_findings"] = ctx["quality_findings"]
    sub["categories"] = ctx["categories"]
    sub["top_category"] = ctx["top_category"]
    sub["domain_analytics"] = ctx["domain_analytics"]
    sub["insights"] = ctx.get("insights", [])
    sub["row_filter"] = ctx.get("transactional_rows")
    sub["forecast"] = ctx.get("forecast")
    return sub


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


def _single_file_section(section: dict[str, Any], ingested) -> dict[str, Any]:
    section.update(_analyze_dataframe(ingested.dataframe if ingested is not None else None))
    if section.get("row_count", 0) == 0 or section.get("columns") == []:
        if ingested is not None and ingested.errors:
            section["errors"] = list(ingested.errors)
        elif not section.get("errors"):
            section["errors"] = ["file contained no data"]
    return section


def _multi_sheet_section(section: dict[str, Any], ingested) -> dict[str, Any]:
    sheets = [_analyze_dataframe(df, sheet=name) for name, df in ingested.sheets.items()]

    section["multi_sheet"] = True
    section["sheet_count"] = len(sheets)
    section["sheets"] = sheets
    section["row_count"] = sum(ss.get("row_count", 0) for ss in sheets)
    columns, seen = [], set()
    for ss in sheets:
        for column in ss.get("columns", []):
            if column not in seen:
                seen.add(column)
                columns.append(column)
    section["columns"] = columns
    section["profile"] = {
        "row_count": section["row_count"],
        "column_count": len(columns),
        "columns": columns,
        "multi_sheet": True,
        "per_sheet": [ss["profile"] for ss in sheets],
    }
    section["classification"] = None
    section["categories"] = None
    section["top_category"] = None
    section["sheet_categories"] = [
        {"sheet": ss["sheet"], "category": ss["top_category"]}
        for ss in sheets
        if ss.get("top_category")
    ]

    scores = [
        ss["data_quality"]["score"]
        for ss in sheets
        if ss.get("data_quality") and ss["data_quality"].get("score") is not None
    ]
    findings = sum(
        ss.get("data_quality", {}).get("findings_count", 0) for ss in sheets
    )
    section["data_quality"] = {
        "score": round(sum(scores) / len(scores), 1) if scores else None,
        "findings_count": findings,
        "multi_sheet": True,
    }
    section["domain_analytics"] = {
        "skipped": "multi-sheet workbook; analyze each sheet separately"
    }
    section["insights"] = []
    filters = [ss.get("row_filter") for ss in sheets if ss.get("row_filter")]
    dropped = sum(f.get("dropped_rows", 0) for f in filters)
    if filters:
        section["row_filter"] = {
            "source_rows": section["row_count"] + dropped,
            "kept_rows": section["row_count"],
            "dropped_rows": dropped,
            "applied": dropped > 0,
            "reason": (
                f"{dropped} structural row(s) across {len(filters)} sheet(s) "
                "carry no product identity and no money value; excluded from "
                "financial aggregates"
                if dropped
                else "every row carried an identity and a money value"
            ),
        }
    return section


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

    if ingested is not None and len(ingested.sheets) > 1:
        return _multi_sheet_section(section, ingested)
    return _single_file_section(section, ingested)


def analyze_application(application, stored_files: list) -> tuple[dict, dict]:
    """Run the full pipeline. Returns ``(summary, report)`` both JSON-safe."""
    file_sections = [_analyze_file(f) for f in stored_files]

    categories_detected: dict[str, list[str]] = {}
    total_rows = 0
    scores = []
    findings_count = sum(len(sec.get("errors", [])) for sec in file_sections)
    for sec in file_sections:
        total_rows += sec.get("row_count", 0)
        sheet_cats = sec.get("sheet_categories")
        if sheet_cats:
            for entry in sheet_cats:
                label = f"{sec['filename']}[{entry['sheet']}]"
                categories_detected.setdefault(entry["category"], []).append(label)
        elif sec.get("top_category"):
            categories_detected.setdefault(sec["top_category"], []).append(sec["filename"])
        dq = sec.get("data_quality")
        if dq and dq.get("score") is not None:
            scores.append(dq["score"])
        findings_count += dq.get("findings_count", 0) if dq else 0

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