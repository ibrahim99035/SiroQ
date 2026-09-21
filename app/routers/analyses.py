"""Retrieval endpoints: saved analysis reports, the printable HTML report, and
raw file download.
"""
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.analytics_service.reporting import build_report_model
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
    slug = value.encode("ascii", "ignore").decode()
    return slug.strip().replace(" ", "-") or fallback


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
        "completed_at": analysis.completed_at.isoformat() if analysis.completed_at else None,
        "summary": analysis.summary,
        "report": analysis.report,
    }


@router.get("/applications/{application_id}/analyses/{analysis_id}/report",
            summary="Generate a report for a saved analysis (HTML or JSON)")
def get_analysis_report(
    application_id: str,
    analysis_id: str,
    format: str = Query("html", pattern="^(html|json)$"),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """Report engine for analysis results.

    ``format=html`` renders a self-contained, printable HTML report (columns,
    quality checks, category probabilities and domain metrics as layout-safe
    CSS bars and tables). ``format=json`` streams the persisted report document
    for machine consumption.
    """
    if not _parse_uuid(application_id) or db.get(Application, application_id) is None:
        raise HTTPException(status_code=404, detail="Application not found")
    if not _parse_uuid(analysis_id):
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = db.get(Analysis, analysis_id)
    if analysis is None or str(analysis.application_id) != application_id:
        raise HTTPException(status_code=404, detail="Analysis not found")

    application = db.get(Application, application_id)
    if format == "json":
        return Response(
            content=json.dumps(analysis.report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="siroq-report-{_ascii_filename(application.name)}-'
                    f'{str(analysis.id)[:8]}.json"'
                )
            },
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
        },
    ).body.decode("utf-8")
    return HTMLResponse(content=body)


@router.get("/files/{file_id}", summary="Download a raw stored file")
def download_file(file_id: str, db: Session = Depends(get_db)):
    if not _parse_uuid(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    stored = db.get(StoredFile, file_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="File not found")
    content = read_bytes(stored.stored_path)
    quoted = stored.original_filename.replace('"', "")
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{quoted}"',
            "X-SHA256": stored.sha256,
        },
    )