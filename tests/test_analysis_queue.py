"""The background analysis queue: claim semantics, terminal states, recovery.

These tests use the module functions directly as well as through HTTP, because
the two halves fail independently: the API surface can be right while two
workers still claim the same job, and that is the bug that matters.
"""
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO

from sqlalchemy import select

from app.analytics_service import queue
from app.database import SessionLocal
from app.models.service_models import Analysis
from tests.conftest import api_headers

CLEAN_SALES_CSV = """sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,cash
2026-09-02,TX-002,Ibuprofen 400mg,1,20.00,20.00,cash
2026-09-03,TX-003,Paracetamol 500mg,3,12.50,37.50,insurance
"""


def _seed_application(client, csv=CLEAN_SALES_CSV):
    """Create an application with one stored file, without running an analysis."""
    r = client.post(
        "/api/v1/applications",
        headers=api_headers(),
        json={"name": "queued-app"},
    )
    assert r.status_code == 201, r.text
    application_id = r.json()["id"]

    r = client.post(
        f"/api/v1/applications/{application_id}/files",
        headers=api_headers(),
        files=[("files", ("sales.csv", BytesIO(csv.encode()), "text/csv"))],
    )
    assert r.status_code == 200, r.text
    return application_id


def _ids(rows):
    """Normalise UUIDs to strings: HTTP hands back strings, the ORM hands back UUIDs."""
    return [str(value) for value in rows]


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------

def test_enqueue_returns_202_with_location_and_queued_status(client):
    application_id = _seed_application(client)

    r = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == queue.STATUS_QUEUED
    assert body["application_id"] == application_id
    # A Location header gives the caller the poll URL without it having to
    # assemble the path and risk getting it subtly wrong.
    assert body["analysis_id"] in r.headers["Location"]
    # summary/report are deliberately absent: null would read as "no data".
    assert "report" not in body


def test_enqueue_rejects_application_with_no_files(client):
    r = client.post("/api/v1/applications", headers=api_headers(), json={"name": "empty"})
    application_id = r.json()["id"]

    r = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )
    # Rejected at the door rather than accepted and then failed by the worker,
    # where the caller would have already been told it succeeded.
    assert r.status_code == 400, r.text


def test_enqueue_unknown_application_is_404(client):
    r = client.post(
        "/api/v1/applications/00000000-0000-0000-0000-000000000000/analyses",
        headers=api_headers(),
    )
    assert r.status_code == 404


def test_report_endpoint_conflicts_while_job_is_not_finished(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    r = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}/report?format=client",
        headers=api_headers(),
    )
    # 200 here would be the worst outcome: a plausible but empty report the
    # caller cannot distinguish from a genuinely empty file.
    assert r.status_code == 409, r.text
    assert queue.STATUS_QUEUED in r.json()["detail"]


def test_polling_endpoint_exposes_started_at_and_error(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    body = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}",
        headers=api_headers(),
    ).json()
    assert body["status"] == queue.STATUS_QUEUED
    assert "started_at" in body
    assert "error_message" in body


# --------------------------------------------------------------------------
# Claim semantics
# --------------------------------------------------------------------------

def test_claim_marks_running_and_records_started_at(client):
    application_id = _seed_application(client)
    client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )

    with SessionLocal() as session:
        claimed = queue.claim_next(session)

    assert claimed is not None
    with SessionLocal() as session:
        row = session.get(Analysis, claimed)
        assert row.status == queue.STATUS_RUNNING
        assert row.started_at is not None


def test_claim_returns_none_when_queue_is_empty(client):
    with SessionLocal() as session:
        assert queue.claim_next(session) is None


def test_claim_is_exclusive_across_sessions(client):
    """The whole point of the claim: two workers must never take one job."""
    application_id = _seed_application(client)
    client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )

    first = SessionLocal()
    second = SessionLocal()
    try:
        a = queue.claim_next(first)
        b = queue.claim_next(second)
    finally:
        first.close()
        second.close()

    assert a is not None
    assert b is None


def test_claim_skips_jobs_already_running(client):
    application_id = _seed_application(client)
    client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )
    with SessionLocal() as session:
        first = queue.claim_next(session)
        assert queue.claim_next(session) is None

    with SessionLocal() as session:
        row = session.get(Analysis, first)
        assert row.status == queue.STATUS_RUNNING


def test_claim_takes_oldest_queued_first(client):
    application_id = _seed_application(client)
    ids = [
        client.post(
            f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
        ).json()["analysis_id"]
        for _ in range(3)
    ]

    order = []
    with SessionLocal() as session:
        while True:
            claimed = queue.claim_next(session)
            if claimed is None:
                break
            order.append(claimed)

    assert _ids(order) == ids


# --------------------------------------------------------------------------
# Terminal states
# --------------------------------------------------------------------------

def test_run_claimed_completes_and_persists_report(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    with SessionLocal() as session:
        assert str(queue.claim_next(session)) == analysis_id
        assert queue.run_claimed(session, analysis_id) == queue.STATUS_COMPLETED

    body = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}",
        headers=api_headers(),
    ).json()
    assert body["status"] == queue.STATUS_COMPLETED
    assert body["report"] is not None
    assert body["completed_at"] is not None

    # And the report is now actually servable.
    r = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}/report?format=client",
        headers=api_headers(),
    )
    assert r.status_code == 200, r.text


def test_run_claimed_records_failure_without_raising(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    # Pull the file out from under the analysis: the pipeline now has nothing to
    # read, which is exactly the shape of a real failure.
    with SessionLocal() as session:
        claimed = queue.claim_next(session)
        from app.models.service_models import StoredFile

        session.query(StoredFile).filter(
            StoredFile.application_id == application_id
        ).delete()
        session.commit()

        assert str(claimed) == analysis_id
        status = queue.run_claimed(session, claimed)

    assert status == queue.STATUS_FAILED
    with SessionLocal() as session:
        row = session.get(Analysis, analysis_id)
        assert row.status == queue.STATUS_FAILED
        assert row.error_message
        assert row.completed_at is not None


def test_run_claimed_on_already_terminal_job_is_a_no_op(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    with SessionLocal() as session:
        queue.claim_next(session)
        queue.run_claimed(session, analysis_id)
        # A duplicate delivery of the same job (at-least-once) must not
        # recompute or overwrite a finished result.
        assert queue.run_claimed(session, analysis_id) == queue.STATUS_COMPLETED


def test_error_message_is_bounded(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    with SessionLocal() as session:
        queue.claim_next(session)
        from app.models.service_models import StoredFile

        session.query(StoredFile).filter(
            StoredFile.application_id == application_id
        ).delete()
        session.commit()
        queue.run_claimed(session, analysis_id)

    with SessionLocal() as session:
        row = session.get(Analysis, analysis_id)
        assert len(row.error_message) <= queue.MAX_ERROR_CHARS


# --------------------------------------------------------------------------
# Crash recovery
# --------------------------------------------------------------------------

def test_requeue_abandoned_returns_stuck_jobs_to_the_queue(client):
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    with SessionLocal() as session:
        queue.claim_next(session)
        # Pretend the worker died: the row is "running" but far older than the
        # staleness threshold.
        session.get(Analysis, analysis_id).started_at = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        )
        session.commit()

        assert queue.requeue_abandoned(session, 3600) == 1

    with SessionLocal() as session:
        row = session.get(Analysis, analysis_id)
        assert row.status == queue.STATUS_QUEUED
        assert row.started_at is None

    # And it is claimable again, so the work is not lost.
    with SessionLocal() as session:
        assert str(queue.claim_next(session)) == analysis_id


def test_requeue_leaves_recently_started_jobs_alone(client):
    """A live job must never be stolen out from under a worker still running it."""
    application_id = _seed_application(client)
    client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )

    with SessionLocal() as session:
        queue.claim_next(session)
        assert queue.requeue_abandoned(session, 3600) == 0

    with SessionLocal() as session:
        # Seeding via /files already produced a completed analysis, so select the
        # job this test is actually about rather than assuming it is the only row.
        row = session.execute(
            select(Analysis).where(
                Analysis.application_id == application_id,
                Analysis.status == queue.STATUS_RUNNING,
            )
        ).scalars().one()
        assert row.status == queue.STATUS_RUNNING


def test_requeue_ignores_jobs_with_no_started_at(client):
    """Defensive: a 'running' row that never got a timestamp cannot be aged."""
    application_id = _seed_application(client)
    analysis_id = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    ).json()["analysis_id"]

    with SessionLocal() as session:
        claimed = queue.claim_next(session)
        session.get(Analysis, claimed).started_at = None
        session.commit()
        assert queue.requeue_abandoned(session, 0) == 0


def test_worker_poll_interval_is_bounded_by_config():
    from app.config import settings

    assert settings.WORKER_POLL_INTERVAL_SECONDS > 0
    # Must exceed the worst measured run (233 s) with room to spare, otherwise a
    # healthy job gets requeued while it is still working.
    assert settings.WORKER_STALE_AFTER_SECONDS > 233
