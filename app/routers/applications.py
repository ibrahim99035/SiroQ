"""Application and analysis endpoints for the SiroQ analysis service."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, Response, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.analytics_service import handoff, ingestion, pipeline, queue, storage
from app.config import settings
from app.database import get_db
from app.models.service_models import Analysis, Application, StoredFile
from app.security import require_api_key

router = APIRouter(prefix="/api/v1", tags=["applications"], dependencies=[Depends(require_api_key)])


class NewApplication(BaseModel):
    name: str
    metadata: dict = Field(default_factory=dict)


class SourceFile(BaseModel):
    original_filename: str
    source_url: str


class RegisterFilesBody(BaseModel):
    files: list[SourceFile]


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
        # Flush per row: bulk (insertmanyvalues) inserts cannot match the
        # sentinel values psycopg returns when both the Uuid pk and the
        # server_default created_at require RETURNING. One insert per flush
        # keeps the round-trips exact.
        session.flush()
        saved.append(stored)
    session.commit()
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


def _get_or_create_application(db: Session, name: str) -> Application:
    """Fetch an application by name, creating it if absent.

    Uses the unique index on ``applications.name`` to stay idempotent under
    concurrent requests: if an identical row wins the insert race, we roll
    back and reuse it instead of failing.
    """
    from sqlalchemy.exc import IntegrityError

    app_obj = db.query(Application).filter(Application.name == name).first()
    if app_obj is not None:
        return app_obj
    app_obj = Application(name=name)
    db.add(app_obj)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        app_obj = db.query(Application).filter(Application.name == name).one()
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

    app_obj = _get_or_create_application(db, application_name.strip())

    _persist_files(db, app_obj.id, uploads)
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)


# --- applications --------------------------------------------------------


@router.get("/applications", summary="List applications with their latest analysis")
def list_applications(db: Session = Depends(get_db)):
    apps = db.query(Application).order_by(Application.created_at.desc()).all()
    out = []
    for app_obj in apps:
        latest = (
            db.query(Analysis)
            .filter(Analysis.application_id == app_obj.id)
            .order_by(Analysis.created_at.desc())
            .first()
        )
        file_count = (
            db.query(StoredFile)
            .filter(StoredFile.application_id == app_obj.id)
            .count()
        )
        analysis_count = (
            db.query(Analysis)
            .filter(Analysis.application_id == app_obj.id)
            .count()
        )
        out.append({
            "id": app_obj.id,
            "name": app_obj.name,
            "metadata": app_obj.metadata_json,
            "created_at": app_obj.created_at.isoformat() if app_obj.created_at else None,
            "file_count": file_count,
            "analysis_count": analysis_count,
            "latest_analysis": (
                {
                    "id": latest.id,
                    "status": latest.status,
                    "created_at": latest.created_at.isoformat()
                    if latest.created_at
                    else None,
                    "completed_at": latest.completed_at.isoformat()
                    if latest.completed_at
                    else None,
                    "summary": latest.summary,
                }
                if latest is not None
                else None
            ),
        })
    return {"applications": out}


@router.post("/applications", status_code=201, summary="Create an empty application")
def create_application(body: NewApplication, db: Session = Depends(get_db)):
    app_obj = _get_or_create_application(db, body.name.strip())
    app_obj.metadata_json = body.metadata or app_obj.metadata_json
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


@router.post("/applications/{application_id}/files", summary="Upload files to an application")
def upload_files(
    application_id: str,
    files: list[UploadFile] = File(...),
    # Register without analysing, so a caller can stage a whole filing and then
    # queue exactly one job for it. Used by the local-storage development path,
    # where files cannot be handed over by URL and so must arrive in the body.
    defer: bool = Query(False),
    db: Session = Depends(get_db),
):
    app_obj = _application_or_404(db, application_id)
    uploads = _read_uploads(files)
    _persist_files(db, app_obj.id, uploads)
    if defer:
        return {
            "application_id": app_obj.id,
            "file_count": len(uploads),
            "status": queue.STATUS_QUEUED,
            "note": "Files registered; queue an analysis to run them.",
        }
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)


@router.post(
    "/applications/{application_id}/files/by-url",
    status_code=201,
    summary="Register files by URL; the worker fetches the bytes when a job runs",
)
def register_files_by_url(
    application_id: str,
    body: RegisterFilesBody,
    db: Session = Depends(get_db),
):
    """Register files without uploading them through this service.

    A caller that already holds the bytes in its own object storage passes
    short-lived presigned GET URLs here. Only the URL is recorded: the transfer
    itself happens in the worker, because the caller is typically a serverless
    function with a wall-clock ceiling, and a 50 MB download inside one would
    time out exactly when the filing is large enough to matter.

    That makes this endpoint fast and idempotent rather than slow and
    byte-consuming. It also means the URLs are validated here but not yet
    exercised: a source that has already expired fails later, on the job, with
    the reason attached.

    Files are only registered. Analysis is a separate explicit
    ``POST /applications/{id}/analyses``, so a caller can stage a whole filing
    and then queue exactly one job for it.
    """
    app_obj = _application_or_404(db, application_id)
    if not body.files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(body.files) > settings.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many files: {len(body.files)} "
                f"(max {settings.MAX_FILES_PER_REQUEST})"
            ),
        )

    registered = []
    for source in body.files:
        # Checked now so an obvious mistake (wrong scheme, wrong host) is reported
        # to the caller immediately instead of surfacing as a failed job later.
        try:
            handoff.check_source_url(source.source_url)
            file_type = ingestion.detect_file_type(source.original_filename)
        except handoff.HandoffError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        row = StoredFile(
            application_id=app_obj.id,
            original_filename=source.original_filename,
            source_url=source.source_url,
            file_type=file_type,
            status=handoff.STATUS_PENDING,
        )
        db.add(row)
        db.flush()  # per-row: see the note in _persist_files about RETURNING
        registered.append(row)
    db.commit()

    return {
        "application_id": app_obj.id,
        "registered": [
            {
                "file_id": row.id,
                "original_filename": row.original_filename,
                "file_type": row.file_type,
                "status": row.status,
            }
            for row in registered
        ],
    }


@router.post("/applications/{application_id}/analyze", summary="Re-run analysis over existing files")
def reanalyze(application_id: str, db: Session = Depends(get_db)):
    app_obj = _application_or_404(db, application_id)
    analysis = _run_and_store_analysis(db, app_obj)
    return _analysis_response(analysis)


def _job_response(analysis: Analysis) -> dict:
    """Status of a queued job. Deliberately omits summary/report — they are null
    until a worker finishes, and a client that treats a null report as 'no data'
    rather than 'not ready' is a bug waiting to happen."""
    return {
        "application_id": analysis.application_id,
        "analysis_id": analysis.id,
        "status": analysis.status,
        "created_at": analysis.created_at.isoformat() if analysis.created_at else None,
    }


@router.post(
    "/applications/{application_id}/analyses",
    status_code=http_status.HTTP_202_ACCEPTED,
    summary="Queue an analysis and return immediately",
)
def enqueue_analysis(
    application_id: str,
    response: Response,
    db: Session = Depends(get_db),
):
    """Enqueue an analysis of an application's stored files.

    The async counterpart to ``/analyze``. Use this for anything large: a
    measured 1M-row run takes ~233 s, which no proxy in a caller's path will hold
    open for. Returns 202 with a job id; poll

        GET /api/v1/applications/{id}/analyses/{analysis_id}

    until ``status`` is ``completed`` (then fetch ``/report?format=client``) or
    ``failed``.
    """
    app_obj = _application_or_404(db, application_id)
    has_files = db.query(StoredFile.id).filter(
        StoredFile.application_id == app_obj.id
    ).first()
    if has_files is None:
        # Fail here rather than let the worker fail later: the caller is still on
        # the request and gets a 400 instead of an accepted job that dies.
        raise HTTPException(status_code=400, detail="Application has no files to analyze")

    analysis = queue.enqueue(db, app_obj.id)
    response.headers["Location"] = (
        f"/api/v1/applications/{app_obj.id}/analyses/{analysis.id}"
    )
    return _job_response(analysis)