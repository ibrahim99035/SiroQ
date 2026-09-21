"""Application and analysis endpoints for the SiroQ analysis service."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analytics_service import ingestion, pipeline, storage
from app.config import settings
from app.database import get_db
from app.models.service_models import Analysis, Application, StoredFile
from app.security import require_api_key

router = APIRouter(prefix="/api/v1", tags=["applications"], dependencies=[Depends(require_api_key)])


class NewApplication(BaseModel):
    name: str
    metadata: dict = Field(default_factory=dict)


def _read_uploads(files: list[UploadFile]) -> list[tuple[str, bytes]]:
    """Validate and buffer uploaded files under the configured caps."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > settings.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files: {len(files)} (max {settings.MAX_FILES_PER_REQUEST})",
        )
    out = []
    for f in files:
        content = f.file.read()
        if len(content) > settings.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File {f.filename!r} exceeds {settings.MAX_FILE_BYTES} bytes",
            )
        out.append((f.filename, content))
    return out


def _persist_files(session: Session, app_id: str, uploads: list[tuple[str, bytes]]) -> list[StoredFile]:
    saved = []
    for filename, content in uploads:
        try:
            file_type = ingestion.detect_file_type(filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rel_path, sha256, size = storage.save_bytes(content, filename)
        stored = StoredFile(
            application_id=app_id,
            original_filename=filename,
            stored_path=rel_path,
            sha256=sha256,
            size_bytes=size,
            file_type=file_type,
        )
        session.add(stored)
        saved.append(stored)
    session.flush()
    return saved


def _run_and_store_analysis(session: Session, app_obj: Application) -> Analysis:
    files = (
        session.query(StoredFile)
        .filter(StoredFile.application_id == app_obj.id)
        .order_by(StoredFile.created_at)
        .all()
    )
    if not files:
        raise HTTPException(status_code=400, detail="Application has no files to analyze")

    analysis = Analysis(application_id=app_obj.id, status="completed")
    try:
        summary, report = pipeline.analyze_application(app_obj, files)
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}")
    analysis.summary = summary
    analysis.report = report
    analysis.completed_at = datetime.now(timezone.utc)
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


def _analysis_response(analysis: Analysis) -> dict:
    return {
        "application_id": analysis.application_id,
        "analysis_id": analysis.id,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
        "summary": analysis.summary,
        "report": analysis.report,
    }


def _application_or_404(db: Session, application_id: str) -> Application:
    """Resolve an id against the UUID primary key; 404 on missing or malformed."""
    try:
        uuid.UUID(application_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Application not found")
    app_obj = db.get(Application, application_id)
    if app_obj is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return app_obj


# --- one-shot: an application + its files in a single request ------------


@router.post("/analyze", summary="Upload an application's files and analyze them in one request")
def analyze_application_one_shot(
    application_name: str = Form(...),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    uploads = _read_uploads(files)
    if application_name.strip() == "":
        raise HTTPException(status_code=400, detail="application_name is required")

    app_obj = (
        db.query(Application).filter(Application.name == application_name.strip()).first()
    )
    if app_obj is None:
        app_obj = Application(name=application_name.strip())
        db.add(app_obj)
        db.flush()

    _persist_files(db, app_obj.id, uploads)
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)


# --- applications --------------------------------------------------------


@router.post("/applications", status_code=201, summary="Create an empty application")
def create_application(body: NewApplication, db: Session = Depends(get_db)):
    app_obj = Application(name=body.name.strip(), metadata_json=body.metadata or {})
    db.add(app_obj)
    db.commit()
    db.refresh(app_obj)
    return {
        "id": app_obj.id,
        "name": app_obj.name,
        "metadata": app_obj.metadata_json,
        "created_at": app_obj.created_at.isoformat() if app_obj.created_at else None,
    }


@router.get("/applications/{application_id}", summary="Application detail: files + analyses summary")
def get_application(application_id: str, db: Session = Depends(get_db)):
    app_obj = _application_or_404(db, application_id)
    files = (
        db.query(StoredFile)
        .filter(StoredFile.application_id == app_obj.id)
        .order_by(StoredFile.created_at)
        .all()
    )
    analyses = (
        db.query(Analysis)
        .filter(Analysis.application_id == app_obj.id)
        .order_by(Analysis.created_at.desc())
        .all()
    )
    return {
        "id": app_obj.id,
        "name": app_obj.name,
        "metadata": app_obj.metadata_json,
        "created_at": app_obj.created_at.isoformat() if app_obj.created_at else None,
        "files": [
            {
                "id": f.id,
                "original_filename": f.original_filename,
                "file_type": f.file_type,
                "size_bytes": f.size_bytes,
                "sha256": f.sha256,
                "status": f.status,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            }
            for f in files
        ],
        "analyses": [
            {
                "id": a.id,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "summary": a.summary,
            }
            for a in analyses
        ],
    }


@router.post("/applications/{application_id}/files", summary="Upload files to an application and analyze")
def upload_files(
    application_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    app_obj = _application_or_404(db, application_id)
    uploads = _read_uploads(files)
    _persist_files(db, app_obj.id, uploads)
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)


@router.post("/applications/{application_id}/analyze", summary="Re-run analysis over existing files")
def reanalyze(application_id: str, db: Session = Depends(get_db)):
    app_obj = _application_or_404(db, application_id)
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)