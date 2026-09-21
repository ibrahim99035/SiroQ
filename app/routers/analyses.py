"""Retrieval endpoints: saved analysis reports and raw file download."""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.analytics_service.storage import read_bytes
from app.database import get_db
from app.models.service_models import Analysis, Application, StoredFile
from app.security import require_api_key

router = APIRouter(
    prefix="/api/v1",
    tags=["analyses"],
    dependencies=[Depends(require_api_key)],
)


def _parse_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


@router.get("/applications/{application_id}/analyses/{analysis_id}", summary="Full saved analysis report")
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