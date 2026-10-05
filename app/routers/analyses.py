"""Retrieval endpoints: saved analysis reports, the printable HTML report, and
raw file download.
"""
import json
import re
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.analytics_service import forecast as forecast_engine
from app.analytics_service import queue
from app.analytics_service.preview import build_series, infer_columns, pick_date_column, pick_value_column, preview
from app.analytics_service.reporting import build_client_projection, build_report_model
from app.analytics_service.storage import read_bytes
from app.database import get_db
from app.models.service_models import Analysis, Application, StoredFile
from app.security import require_api_key

router = APIRouter(
    prefix="/api/v1",
    tags=["analyses"],
    dependencies=[Depends(require_api_key)],
)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _parse_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _ascii_filename(value: str, fallback: str = "app") -> str:
    """Reduce an application name to a safe ASCII filename stem.

    Application names are free text and end up inside a quoted
    ``Content-Disposition`` filename. A name containing ``"`` or a newline would
    corrupt that header (and a name with only non-ASCII characters, such as the
    Arabic samples, must still yield a usable stem rather than nothing).
    """
    slug = value.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", slug).strip("-._")
    # a stem of only dots would resolve to the parent directory
    return slug or fallback


def _json_download(payload: Any, stem: str, analysis_id: Any) -> Response:
    """Serialize ``payload`` as a downloadable JSON attachment.

    Both the raw report (``format=json``) and the SiroQ-Client projection
    (``format=client``) go through here so the filename contract and encoding
    are identical for every JSON consumer.
    """
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{stem}-{str(analysis_id)[:8]}.json"'
            )
        },
    )


def _require_report(analysis: Analysis) -> None:
    """Refuse to render a report for an analysis that has not produced one.

    A queued or running analysis has ``report is None``. Building a projection
    from that would return a plausible-looking but empty 200 — the worst kind of
    failure for a caller, because it cannot tell "not ready yet" from "the file
    contained nothing". 409 tells it to poll.
    """
    if analysis.report is not None:
        return
    if analysis.status in queue.TERMINAL_STATUSES:
        detail = analysis.error_message or f"Analysis {analysis.status}."
    else:
        detail = f"Analysis is {analysis.status}; no report is available yet."
    raise HTTPException(status_code=409, detail=detail)


@router.get("/applications/{application_id}/analyses/{analysis_id}",
            summary="Full saved analysis report")
def get_analysis(application_id: str, analysis_id: str, db: Session = Depends(get_db)):
    if not _parse_uuid(application_id) or db.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _parse_uuid(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = db.get(Analysis, analysis_id)
    if analysis is None or str(analysis.application_id) != application_id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return {
        "application_id": analysis.application_id,
        "analysis_id": analysis.id,
        "status": analysis.status,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        "started_at": analysis.started_at.isoformat() if analysis.started_at else None,
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        # Only meaningful for a failed run, but a caller polling a job needs the
        # reason it died rather than just a status it already guessed.
        "error_message": analysis.error_message,
        "summary": analysis.summary,
        "report": analysis.report,
    }


@router.get("/applications/{application_id}/analyses/{analysis_id}/report",
            summary="Generate a report for a saved analysis (HTML or JSON)")
def get_analysis_report(
    application_id: str,
    analysis_id: str,
    format: str = Query("html", pattern="^(html|json|client)$"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Report engine for analysis results.

    ``format=html`` renders a self-contained, printable HTML report (columns,
    quality checks, category probabilities and domain metrics as layout-safe
    CSS bars and tables). ``format=json`` streams the persisted report document
    for machine consumption. ``format=client`` streams the decision-grade
    projection SiroQ-Client renders (``ReportResultData``): flat pre-formatted
    metrics at the top level with per-file drill-down branches.
    """
    if not _parse_uuid(application_id) or db.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _parse_uuid(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = db.get(Analysis, analysis_id)
    if analysis is None or str(analysis.application_id) != application_id:
        raise HTTPException(status_code=404, detail="Analysis not found")
    _require_report(analysis)

    application = db.get(Application, application_id)
    if format == "json":
        return _json_download(
            analysis.report,
            f"siroq-report-{_ascii_filename(application.name)}",
            analysis.id,
        )
    if format == "client":
        return _json_download(
            build_client_projection(
                analysis.report, analysis.summary, analysis_id=analysis.id
            ),
            f"siroq-client-{_ascii_filename(application.name)}",
            analysis.id,
        )
    body = TEMPLATES.TemplateResponse(
        request,
        "report.html",
        {
            "application": {
                "id": application.id,
                "name": application.name,
                "created_at": (
                    application.created_at.isoformat()
                    if application.created_at
                    else None
                ),
            },
            "analysis": {
                "id": analysis.id,
                "status": analysis.status,
                "created_at": (
                    analysis.created_at.isoformat() if analysis.created_at else None
                ),
                "completed_at": (
                    analysis.completed_at.isoformat()
                    if analysis.completed_at
                    else None
                ),
            },
            "model": build_report_model(analysis.report, analysis.summary),
            "report": analysis.report,
            "client_url": (
                f"/api/v1/applications/{application_id}/analyses/{analysis_id}"
                "/report?format=client"
            ),
            "raw_url": (
                f"/api/v1/applications/{application_id}/analyses/{analysis_id}"
                "/report?format=json"
            ),
        },
    ).body.decode("utf-8")
    return HTMLResponse(content=body)


def content_disposition(filename: str) -> str:
    """Build a download header that survives a non-ASCII filename.

    Filings here are frequently Arabic, and a header carrying raw UTF-8 is not
    reliably decoded by every client: some read the bytes as latin-1 and produce a
    mangled or outright invalid name. Two forms are therefore always sent.

    The quoted `filename=` is ASCII-only, with characters that are illegal in a
    quoted-string removed, so old clients get *something* rather than a parse
    error. The real name rides along in `filename*=`, which is RFC 5987's
    percent-encoded form and what every current browser actually prefers.
    Percent-encoding happens after the charset prefix is applied to the whole
    value, and `safe=""` is deliberate: it escapes `/` too, because a filename is
    one path segment and must not be able to introduce another.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    ascii_name = re.sub(r'[^\x20-\x7e"\\;]', "_", ascii_name).replace('"', "").replace("\\", "")
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


@router.get("/files/{file_id}", summary="Download a raw stored file")
def download_file(file_id: str, db: Session = Depends(get_db)):
    if not _parse_uuid(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    stored = db.get(StoredFile, file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found")
    content = read_bytes(stored.stored_path)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(stored.original_filename),
            "X-SHA256": stored.sha256,
        },
    )


def _stored_or_404(file_id: str, db: Session) -> StoredFile:
    if not _parse_uuid(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    stored = db.get(StoredFile, file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found")
    return stored


@router.get("/files/{file_id}/preview", summary="Data-source preview: columns + sample rows")
def file_preview(
    file_id: str,
    sheet: str | None = Query(None, description="Sheet name or 0-based index for Excel files"),
    rows: int = Query(25, ge=1, le=200, description="Rows per page"),
    offset: int = Query(0, ge=0, description="Row offset for pagination"),
    db: Session = Depends(get_db),
):
    """Introspect a stored raw file live: dtypes, columns, and (paged) rows.

    Unlike the persisted analysis report, this always reflects the exact bytes
    on disk (which sheet to read is selectable for multi-sheet workbooks).
    """
    stored = _stored_or_404(file_id, db)
    try:
        return preview(stored, sheet=sheet, rows=rows, offset=offset)
    except Exception as exc:  # defensive: bad bytes must not 500
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")


@router.get("/files/{file_id}/series", summary="Build a daily time series from a stored file")
def file_series(
    file_id: str,
    sheet: str | None = Query(None, description="Sheet name or 0-based index for Excel files"),
    date_col: str | None = Query(None, description="Date column (autodetected when omitted)"),
    value_col: str | None = Query(None, description="Numeric value column (autodetected)"),
    agg: str = Query("sum", pattern="^(sum|mean|count)$"),
    db: Session = Depends(get_db),
):
    """Aggregate any date+value columns of a stored file into a daily series."""
    stored = _stored_or_404(file_id, db)
    try:
        from app.analytics_service.preview import load_file, select_frame

        ing = load_file(stored)
        df, sheet_label = select_frame(ing, sheet)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")
    if df.empty:
        raise HTTPException(status_code=400, detail="No rows available in this file/sheet.")

    date_column = pick_date_column(df, date_col or None)
    if date_column is None:
        raise HTTPException(
            status_code=400,
            detail="No date/time column found. Forecasting needs a date column.",
        )
    value_column = pick_value_column(df, value_col or None)
    if value_column is None:
        agg = "count"
    series, meta = build_series(df, date_column, value_column, agg)
    if not series:
        raise HTTPException(
            status_code=400,
            detail="Could not build a series: no parseable dates in the selected column.",
        )
    meta.update({
        "file_id": file_id,
        "filename": stored.original_filename,
        "sheet": sheet_label,
        "sheets": list(ing.sheets),
    })
    return {"series": series, "meta": meta, "columns": infer_columns(df)}


@router.get("/files/{file_id}/forecast", summary="Pure statistical forecast over a stored file")
def file_forecast(
    file_id: str,
    sheet: str | None = Query(None, description="Sheet name or 0-based index for Excel files"),
    date_col: str | None = Query(None, description="Date column (autodetected when omitted)"),
    value_col: str | None = Query(None, description="Numeric value column (autodetected)"),
    agg: str = Query("sum", pattern="^(sum|mean|count)$"),
    horizon: int = Query(14, ge=1, le=365, description="Forecast horizon in days"),
    confidence: float = Query(0.90, gt=0, lt=1, description="Prediction-interval confidence"),
    db: Session = Depends(get_db),
):
    """Non-AI forecasting over the live data source (no persisted analysis).

    Classical statistical methods only (naive / moving average / linear trend /
    weekly seasonality / damped Holt), method chosen by a holdout, plus a
    prediction band — fully deterministic and reproducible.
    """
    stored = _stored_or_404(file_id, db)
    try:
        from app.analytics_service.preview import load_file, select_frame

        ing = load_file(stored)
        df, sheet_label = select_frame(ing, sheet)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}")
    if df.empty:
        raise HTTPException(status_code=400, detail="No rows available in this file/sheet.")

    date_column = pick_date_column(df, date_col or None)
    if date_column is None:
        raise HTTPException(
            status_code=400,
            detail="No date/time column found. Forecasting needs a date column.",
        )
    value_column = pick_value_column(df, value_col or None)
    if value_column is None:
        agg = "count"
    series, meta = build_series(df, date_column, value_column, agg)
    if not series:
        raise HTTPException(
            status_code=400,
            detail="Could not build a series: no parseable dates in the selected column.",
        )
    if len(series) < 2:
        raise HTTPException(
            status_code=400,
            detail="Need at least two dates of history to forecast.",
        )

    result = forecast_engine.forecast(
        [p["value"] for p in series],
        dates=[p["date"] for p in series],
        horizon=horizon,
        confidence=confidence,
    )
    meta.update({
        "file_id": file_id,
        "filename": stored.original_filename,
        "sheet": sheet_label,
        "sheets": list(ing.sheets),
    })
    result["meta"] = meta
    result["method_description"] = forecast_engine.describe(result.get("method"))
    return result