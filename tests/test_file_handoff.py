"""Handing files over by presigned URL instead of uploading them through the API.

The point of this endpoint is that a client on a different storage namespace can
pass its bytes over without sharing credentials and without proxying tens of
megabytes through every hop. The point of these tests is the two things that
makes necessary: the URL fetch is guarded, and the bytes move in the worker
rather than in the caller's request.
"""
import pytest

from app.analytics_service import handoff
from app.config import settings
from tests.conftest import api_headers

SALES_CSV = b"""sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,cash
2026-09-02,TX-002,Ibuprofen 400mg,1,20.00,20.00,cash
2026-09-03,TX-003,Paracetamol 500mg,3,12.50,37.50,insurance
"""

ALLOWED_HOST = "files.example.test"


class FakeStream:
    """Stands in for httpx.stream so the guards are tested without a network."""

    def __init__(self, chunks, status_code=200):
        self._chunks = chunks
        self.status_code = status_code

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_bytes(self):
        yield from self._chunks


@pytest.fixture
def allow_client_storage(monkeypatch):
    """Pretend the client's object store is the one approved source."""
    monkeypatch.setattr(settings, "SOURCE_URL_ALLOWED_HOSTS", ALLOWED_HOST)
    return ALLOWED_HOST


@pytest.fixture
def fake_fetch(monkeypatch):
    """Replace the network with a scripted response, and record the calls."""

    def install(chunks=(SALES_CSV,), status_code=200):
        calls = []

        def _stream(method, url, **kwargs):
            calls.append({"method": method, "url": url, **kwargs})
            return FakeStream(list(chunks), status_code=status_code)

        monkeypatch.setattr("app.analytics_service.handoff.httpx.stream", _stream)
        return calls

    return install


def _application(client, name="handoff"):
    r = client.post("/api/v1/applications", headers=api_headers(), json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _register_many(client, application_id, pairs):
    """Register several files in one request: a list of ``(url, filename)``."""
    return client.post(
        f"/api/v1/applications/{application_id}/files/by-url",
        headers=api_headers(),
        json={
            "files": [
                {"original_filename": name, "source_url": url} for url, name in pairs
            ]
        },
    )


def _register(client, application_id, url, filename="sales.csv"):
    return _register_many(client, application_id, [(url, filename)])


def _run(analysis_id):
    from app.analytics_service import queue
    from app.database import SessionLocal

    with SessionLocal() as session:
        queue.claim_next(session)
        return queue.run_claimed(session, analysis_id)


def _enqueue(client, application_id):
    r = client.post(
        f"/api/v1/applications/{application_id}/analyses", headers=api_headers()
    )
    assert r.status_code == 202, r.text
    return r.json()["analysis_id"]


# --------------------------------------------------------------------------
# The guards, applied at registration
# --------------------------------------------------------------------------

def test_rejects_plain_http(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    r = _register(client, application_id, f"http://{ALLOWED_HOST}/sales.csv")
    assert r.status_code == 400
    assert "https" in r.json()["detail"]


def test_rejects_link_local_metadata_address(client, allow_client_storage, fake_fetch):
    """The reason the allowlist exists: 169.254.169.254 hands out credentials."""
    application_id = _application(client)
    r = _register(client, application_id, "https://169.254.169.254/latest/meta-data/")
    assert r.status_code == 400
    assert "not an allowed source" in r.json()["detail"]


def test_rejects_disallowed_host(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    r = _register(client, application_id, "https://evil.example.org/sales.csv")
    assert r.status_code == 400


def test_rejects_host_that_merely_ends_with_an_allowed_one(client, allow_client_storage, fake_fetch):
    """`files.example.test.evil.org` must not pass a suffix or substring test."""
    application_id = _application(client)
    r = _register(client, application_id, f"https://{ALLOWED_HOST}.evil.org/sales.csv")
    assert r.status_code == 400


def test_rejects_url_with_no_host(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    r = _register(client, application_id, "https:///sales.csv")
    assert r.status_code == 400


def test_rejects_empty_file_list(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    r = client.post(
        f"/api/v1/applications/{application_id}/files/by-url",
        headers=api_headers(),
        json={"files": []},
    )
    assert r.status_code == 400


def test_rejects_unsupported_extension(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    r = _register(
        client, application_id, f"https://{ALLOWED_HOST}/notes.pdf", filename="notes.pdf"
    )
    assert r.status_code == 400


def test_allowed_hosts_default_to_this_services_own_object_store(monkeypatch):
    monkeypatch.setattr(settings, "SOURCE_URL_ALLOWED_HOSTS", "")
    monkeypatch.setattr(settings, "S3_ENDPOINT", "https://bucket.storage.example.test")
    assert handoff.allowed_hosts() == {"bucket.storage.example.test"}


def test_allowed_hosts_are_empty_and_deny_everything_without_an_endpoint(monkeypatch):
    """Fail closed: no configured endpoint must mean no fetching, not any host."""
    monkeypatch.setattr(settings, "SOURCE_URL_ALLOWED_HOSTS", "")
    monkeypatch.setattr(settings, "S3_ENDPOINT", "")
    assert handoff.allowed_hosts() == set()


# --------------------------------------------------------------------------
# Registration records the URL and returns immediately
# --------------------------------------------------------------------------

def test_registration_does_not_download_anything(client, allow_client_storage, fake_fetch):
    """The whole reason the transfer moved to the worker: a filing-sized download
    inside a serverless request is a timeout waiting to happen."""
    application_id = _application(client)
    calls = fake_fetch()

    r = _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    assert r.status_code == 201, r.text
    assert calls == []


def test_registered_file_is_pending_and_knows_nothing_about_its_bytes(
    client, allow_client_storage, fake_fetch
):
    application_id = _application(client)
    fake_fetch()
    body = _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv").json()

    entry = body["registered"][0]
    assert entry["status"] == handoff.STATUS_PENDING
    assert entry["original_filename"] == "sales.csv"
    # No placeholder zero: a pending file genuinely does not know its size yet.
    assert "sha256" not in entry
    assert "size_bytes" not in entry


def test_registering_does_not_start_an_analysis_on_its_own(client, allow_client_storage, fake_fetch):
    """Staging and queueing are separate decisions, so a caller can register a
    whole filing and then queue exactly one job for it."""
    application_id = _application(client)
    fake_fetch()
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")

    detail = client.get(
        f"/api/v1/applications/{application_id}", headers=api_headers()
    ).json()
    assert detail["analyses"] == []


def test_enqueue_accepts_an_application_whose_files_are_still_pending(
    client, allow_client_storage, fake_fetch
):
    """Registration is not analysis: the bytes are not here yet, but the job is
    still valid and the worker will collect them."""
    application_id = _application(client)
    fake_fetch()
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    assert _enqueue(client, application_id)


# --------------------------------------------------------------------------
# The worker collects the bytes
# --------------------------------------------------------------------------

def test_worker_fetches_and_stores_the_file(client, allow_client_storage, fake_fetch):
    application_id = _application(client)
    calls = fake_fetch()
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    analysis_id = _enqueue(client, application_id)

    assert _run(analysis_id) == "completed"
    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    # Redirects are the cheapest way past a check applied only to the first hop.
    assert calls[0]["follow_redirects"] is False

    files = client.get(
        f"/api/v1/applications/{application_id}", headers=api_headers()
    ).json()["files"]
    assert files[0]["status"] == handoff.STATUS_STORED
    assert files[0]["size_bytes"] == len(SALES_CSV)
    assert len(files[0]["sha256"]) == 64


def test_worker_clears_the_source_url_once_the_bytes_are_ours(
    client, allow_client_storage, fake_fetch
):
    """A signed URL should not linger in the database where it can be re-used."""
    from app.models.service_models import StoredFile
    from app.database import SessionLocal

    application_id = _application(client)
    fake_fetch()
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    analysis_id = _enqueue(client, application_id)
    _run(analysis_id)

    with SessionLocal() as session:
        row = session.query(StoredFile).filter(
            StoredFile.application_id == application_id
        ).one()
        assert row.source_url is None


def test_worker_only_fetches_files_that_are_still_pending(
    client, allow_client_storage, fake_fetch
):
    """A second run must not re-download bytes this service already has."""
    application_id = _application(client)
    calls = fake_fetch()
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")

    first = _enqueue(client, application_id)
    assert _run(first) == "completed"
    assert len(calls) == 1

    second = _enqueue(client, application_id)
    assert _run(second) == "completed"
    # One fetch, across two analyses.
    assert len(calls) == 1


def test_worker_fetches_each_file_of_a_multi_file_filing(
    client, allow_client_storage, fake_fetch
):
    application_id = _application(client)
    calls = fake_fetch()
    client.post(
        f"/api/v1/applications/{application_id}/files/by-url",
        headers=api_headers(),
        json={
            "files": [
                {"original_filename": "a.csv", "source_url": f"https://{ALLOWED_HOST}/a.csv"},
                {"original_filename": "b.csv", "source_url": f"https://{ALLOWED_HOST}/b.csv"},
            ]
        },
    )
    analysis_id = _enqueue(client, application_id)

    assert _run(analysis_id) == "completed"
    assert len(calls) == 2

    detail = client.get(
        f"/api/v1/applications/{application_id}", headers=api_headers()
    ).json()
    assert {f["status"] for f in detail["files"]} == {handoff.STATUS_STORED}


# --------------------------------------------------------------------------
# Failure modes, on the job rather than the request
# --------------------------------------------------------------------------

def test_expired_source_fails_the_job_with_the_reason(client, allow_client_storage, fake_fetch):
    """The URL was fine at registration and dead by the time it was used. That is
    a failed job with a reason, not a 200 and an empty report."""
    application_id = _application(client)
    fake_fetch(status_code=403)
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    analysis_id = _enqueue(client, application_id)

    assert _run(analysis_id) == "failed"

    body = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}",
        headers=api_headers(),
    ).json()
    assert "403" in body["error_message"]

    # And no report is served for a job that never produced one.
    r = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}/report?format=client",
        headers=api_headers(),
    )
    assert r.status_code == 409


def test_size_cap_is_enforced_while_streaming(client, allow_client_storage, fake_fetch, monkeypatch):
    """Capped mid-stream, so a hostile source cannot make this allocate to death."""
    monkeypatch.setattr(settings, "MAX_FILE_BYTES", 16)
    application_id = _application(client)
    fake_fetch(chunks=[b"x" * 10, b"y" * 10])
    _register(client, application_id, f"https://{ALLOWED_HOST}/big.csv")
    analysis_id = _enqueue(client, application_id)

    assert _run(analysis_id) == "failed"
    body = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}",
        headers=api_headers(),
    ).json()
    assert "exceeds" in body["error_message"]


def test_a_failed_fetch_leaves_no_half_written_row(
    client, allow_client_storage, fake_fetch
):
    """Nothing is half-written, and nothing dead is left behind.

    The row only existed to carry the URL. Once the fetch is impossible the URL
    is worth nothing, so the row goes with it -- otherwise it is the oldest
    pending row on the next attempt, every retry fails on it first, and the
    files that *are* still fetchable never get reached.
    """
    from app.models.service_models import StoredFile
    from app.database import SessionLocal

    application_id = _application(client)
    fake_fetch(status_code=500)
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    analysis_id = _enqueue(client, application_id)
    assert _run(analysis_id) == "failed"

    with SessionLocal() as session:
        assert session.query(StoredFile).filter(
            StoredFile.application_id == application_id
        ).count() == 0


def test_reregistering_after_a_failed_fetch_recovers(
    client, allow_client_storage, fake_fetch
):
    """The recovery path the row deletion exists for: the caller registers the
    file again with a URL that works, and the same analysis now succeeds."""
    application_id = _application(client)
    fake_fetch(status_code=500)
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")
    assert _run(_enqueue(client, application_id)) == "failed"

    fake_fetch(status_code=200, chunks=[b"region,total\nnorth,10\n"])
    _register(client, application_id, f"https://{ALLOWED_HOST}/sales.csv")

    analysis_id = _enqueue(client, application_id)
    assert _run(analysis_id) == "completed"

    report = client.get(
        f"/api/v1/applications/{application_id}/analyses/{analysis_id}/report?format=client",
        headers=api_headers(),
    )
    assert report.status_code == 200


def test_one_unreachable_file_does_not_strand_the_reachable_ones(
    client, allow_client_storage, fake_fetch
):
    """A filing where the first file is dead and the second was never tried.

    The dead row goes; the one that was never attempted stays pending, so the
    retry is a normal pass over what is left rather than a fresh start.
    """
    from app.models.service_models import StoredFile
    from app.database import SessionLocal

    application_id = _application(client)
    fake_fetch(status_code=500)
    _register_many(
        client,
        application_id,
        [
            (f"https://{ALLOWED_HOST}/dead.csv", "dead.csv"),
            (f"https://{ALLOWED_HOST}/live.csv", "live.csv"),
        ],
    )
    assert _run(_enqueue(client, application_id)) == "failed"

    with SessionLocal() as session:
        rows = (
            session.query(StoredFile)
            .filter(StoredFile.application_id == application_id)
            .all()
        )
        assert [row.original_filename for row in rows] == ["live.csv"]
        assert rows[0].status == handoff.STATUS_PENDING
        assert rows[0].stored_path is None

    # Only the missing file has to be registered again; `live.csv` is already
    # staged and is fetched by the retry.
    fake_fetch(status_code=200, chunks=[b"region,total\nnorth,10\n"])
    _register(client, application_id, f"https://{ALLOWED_HOST}/dead.csv", filename="dead.csv")

    assert _run(_enqueue(client, application_id)) == "completed"

    with SessionLocal() as session:
        rows = (
            session.query(StoredFile)
            .filter(StoredFile.application_id == application_id)
            .all()
        )
        assert all(row.source_url is None for row in rows)
        assert all(row.stored_path is not None for row in rows)
