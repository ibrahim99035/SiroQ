"""Durable job queue for analyses.

Analysis is a multi-second-to-multi-minute, CPU-bound, memory-hungry operation
(see docs/CAPACITY.md: a 1M-row file measures 233 s and 717 MB peak RSS). Running
it inside a request handler means the caller is at the mercy of every proxy in
the path — a hosting provider's load balancer, a CDN, the calling application's
own function timeout — and a dropped connection silently loses the work even
though the server may finish it.

So the work moves to a background worker that polls this table. The queue is
Postgres rather than Redis or Celery because the database is already a required,
already-backed-up dependency: adding a second datastore to gain at-most-once
semantics would be a poor trade for a single-node worker. The atomic claim below
is the part that actually provides those semantics.

Lifecycle::

    queued ──claim──▶ running ──▶ completed
                          │
                          └──▶ failed
                          │
     (process died) ─────┘
                   requeue_abandoned() ──▶ queued
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.analytics_service import handoff, pipeline
from app.models.service_models import Analysis, Application, StoredFile

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

#: A job in one of these states will not change again on its own.
TERMINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED})

#: Error text is unbounded in a TEXT column, but a giant message is useless in an
#: API response and bloats every read of the row.
MAX_ERROR_CHARS = 2000


def enqueue(session: Session, application_id: str) -> Analysis:
    """Record a pending analysis for an application and return it unread.

    No flush: the caller decides when to commit, and the id is not needed by the
    caller (the HTTP response carries status, not the row id).
    """
    analysis = Analysis(application_id=application_id, status=STATUS_QUEUED)
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis


def claim_next(session: Session) -> str | None:
    """Atomically take the oldest queued analysis, or return None if there is none.

    Two things make this safe with more than one worker running:

    * ``FOR UPDATE SKIP LOCKED`` — a worker that finds the row already locked
      skips it and takes the next one instead of blocking, so N workers process N
      jobs rather than queueing behind each other.
    * the ``status = 'queued'`` predicate repeated in the UPDATE — even if two
      workers somehow read the same candidate, the row is only moved to
      ``running`` once. The loser gets ``None`` back.
    """
    candidate = session.execute(
        select(Analysis.id)
        .where(Analysis.status == STATUS_QUEUED)
        .order_by(Analysis.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if candidate is None:
        return None

    claimed = session.execute(
        update(Analysis)
        .where(Analysis.id == candidate, Analysis.status == STATUS_QUEUED)
        .values(status=STATUS_RUNNING, started_at=datetime.now(timezone.utc))
        .returning(Analysis.id)
    ).scalar_one_or_none()
    session.commit()
    return claimed


def requeue_abandoned(session: Session, max_age_seconds: int) -> int:
    """Return jobs stuck in ``running`` to the queue. Returns how many.

    Called on worker startup and periodically thereafter. A worker killed by a
    deploy or an OOM never gets to write a terminal status, so without this its
    job would sit in ``running`` forever and its caller would poll until it gave
    up.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    result = session.execute(
        update(Analysis)
        .where(
            Analysis.status == STATUS_RUNNING,
            Analysis.started_at.is_not(None),
            Analysis.started_at < cutoff,
        )
        .values(status=STATUS_QUEUED, started_at=None, error_message=None)
    )
    session.commit()
    requeued = result.rowcount or 0
    if requeued:
        logger.warning("requeued %d abandoned analysis job(s)", requeued)
    return requeued


def _files_for(session: Session, application_id: str) -> list[StoredFile]:
    """Stored files for an application, oldest first.

    Duplicated from the synchronous path in app/routers/applications.py on
    purpose: that path reports failure as an HTTP error, this one records it as a
    failed row, and collapsing them would mean threading an error-handling mode
    through the pipeline for no benefit. Keep the ordering clause in sync.
    """
    return (
        session.query(StoredFile)
        .filter(StoredFile.application_id == application_id)
        .order_by(StoredFile.created_at)
        .all()
    )


def run_claimed(session: Session, analysis_id: str) -> str:
    """Run one claimed analysis to a terminal state. Returns the final status.

    Always terminates in ``completed`` or ``failed`` — an unhandled exception
    escaping here would leave the row in ``running`` and the worker loop would
    still move on, so the caller must be able to rely on that.
    """
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        logger.warning("claimed analysis %s no longer exists; skipping", analysis_id)
        return STATUS_FAILED
    if analysis.status in TERMINAL_STATUSES:
        logger.info("analysis %s already %s; skipping", analysis_id, analysis.status)
        return analysis.status

    application = session.get(Application, analysis.application_id)
    if application is None:
        return fail(session, analysis_id, "Application no longer exists")

    files = _files_for(session, application.id)
    if not files:
        return fail(session, analysis_id, "Application has no files to analyze")

    # Files registered from a handed-off URL arrive as rows with no bytes yet.
    # Fetch them now, on the worker, where there is no request deadline. Doing it
    # in the caller's request instead would put a whole filing's download inside
    # a serverless function's wall-clock budget.
    try:
        handoff.materialize_pending_files(session, application.id)
    except handoff.HandoffError as exc:
        session.rollback()
        return fail(session, analysis_id, f"Could not fetch source file: {exc}")
    except Exception as exc:  # noqa: BLE001 - a bad file must not kill the worker
        logger.exception("analysis %s failed fetching handed-off files", analysis_id)
        session.rollback()
        return fail(session, analysis_id, f"{type(exc).__name__}: {exc}")

    # Re-read: materialisation may have committed rows, and the pipeline wants
    # StoredFile rows that carry a real stored_path rather than a pending one.
    files = _files_for(session, application.id)

    started = datetime.now(timezone.utc)
    try:
        summary, report = pipeline.analyze_application(application, files)
    except Exception as exc:  # noqa: BLE001 - a bad file must not kill the worker
        logger.exception("analysis %s failed", analysis_id)
        session.rollback()
        return fail(session, analysis_id, f"{type(exc).__name__}: {exc}")

    # Re-read: a long run may have outlived the loaded object's session state.
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        logger.warning("analysis %s vanished mid-run; discarding result", analysis_id)
        return STATUS_FAILED

    analysis.summary = summary
    analysis.report = report
    analysis.status = STATUS_COMPLETED
    analysis.error_message = None
    analysis.started_at = started
    analysis.completed_at = datetime.now(timezone.utc)
    session.add(analysis)
    session.commit()
    logger.info(
        "analysis %s completed in %.1fs",
        analysis_id,
        (analysis.completed_at - started).total_seconds(),
    )
    return STATUS_COMPLETED


def fail(session: Session, analysis_id: str, message: str) -> str:
    """Mark an analysis failed. Used after a rollback, so it re-reads the row."""
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        return STATUS_FAILED
    analysis.status = STATUS_FAILED
    analysis.error_message = message[:MAX_ERROR_CHARS]
    analysis.completed_at = datetime.now(timezone.utc)
    session.add(analysis)
    session.commit()
    return STATUS_FAILED
