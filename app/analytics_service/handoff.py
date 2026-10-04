"""Cross-service file handoff: fetching a caller's object into our own storage.

The client keeps uploads in its own object-storage namespace. On Neon a bucket is
scoped to a branch, so its objects live somewhere this service holds no
credentials for — it cannot be handed a key and told to go read it. What it can
be handed is a short-lived presigned GET URL, which this service fetches itself.
Neither side shares a credential, and neither side proxies the file.

Two properties this module exists to guarantee:

*   **The fetch is guarded.** A caller-supplied URL turns this into a request
    forger, so: https only, host must be on an explicit allowlist, redirects are
    never followed (a redirect is the cheapest way past a check applied only to
    the first hop), and the size cap is enforced while streaming so a hostile
    source cannot make the process allocate its way to an OOM.

*   **The download happens in the worker, not in a request.** The party handing
    over a filing is a serverless function with a wall-clock ceiling, and a
    50 MB transfer through one times out at exactly the moment the product
    matters most. So registering a file records only the URL — enqueueing stays
    instant — and the bytes move when a job is claimed.
"""
import logging
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics_service import storage
from app.config import settings
from app.models.service_models import StoredFile

logger = logging.getLogger("siroq.handoff")

#: ``StoredFile.status`` while the bytes are still at the source.
STATUS_PENDING = "pending"
STATUS_STORED = "stored"


class HandoffError(Exception):
    """A handoff could not be completed. Message is safe to show a caller."""


def allowed_hosts() -> set[str]:
    """Hosts this service will fetch a caller's file from.

    Blank configuration means "this service's own object store". With no
    configured endpoint at all the answer is the empty set, which denies every
    host: failing closed is the only safe reading of an unset allowlist.
    """
    configured = settings.SOURCE_URL_ALLOWED_HOSTS.strip()
    if configured:
        return {host.strip().lower() for host in configured.split(",") if host.strip()}
    endpoint_host = urlparse(settings.S3_ENDPOINT).hostname
    return {endpoint_host.lower()} if endpoint_host else set()


def check_source_url(url: str) -> None:
    """Raise :class:`HandoffError` unless this URL is safe to fetch."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise HandoffError(f"Source URL must be https, got {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    # Exact match against the allowlist. A substring or suffix test would accept
    # `allowed.example.evil.org`, which is the whole attack.
    if not host or host not in allowed_hosts():
        raise HandoffError(f"Source host {host or '(none)'!r} is not an allowed source")


def fetch_source_bytes(url: str, filename: str, max_bytes: int | None = None) -> bytes:
    """Download one file from a caller-supplied URL, subject to the guards above."""
    check_source_url(url)
    limit = settings.MAX_FILE_BYTES if max_bytes is None else max_bytes

    chunks: list[bytes] = []
    total = 0
    try:
        with httpx.stream(
            "GET", url, timeout=settings.SOURCE_FETCH_TIMEOUT_SECONDS, follow_redirects=False
        ) as response:
            if response.status_code >= 400:
                raise HandoffError(
                    f"Source returned HTTP {response.status_code} for {filename!r}"
                )
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    raise HandoffError(f"{filename!r} exceeds {limit} bytes")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise HandoffError(f"Could not fetch {filename!r}: {exc}") from exc

    return b"".join(chunks)


def pending_files(session: Session, application_id: str) -> list[StoredFile]:
    """Files registered from a URL whose bytes have not arrived yet."""
    return list(
        session.execute(
            select(StoredFile)
            .where(
                StoredFile.application_id == application_id,
                StoredFile.source_url.is_not(None),
            )
            .order_by(StoredFile.created_at)
        )
        .scalars()
        .all()
    )


def materialize_pending_files(session: Session, application_id: str) -> int:
    """Fetch every still-pending file into our own storage. Returns the count.

    Each file is committed as it lands, so a worker that dies halfway through a
    five-file filing does not refetch the four that already arrived.
    """
    rows = pending_files(session, application_id)
    for row in rows:
        url = row.source_url
        logger.info("fetching handed-off file %s (%s)", row.original_filename, row.id)
        try:
            content = fetch_source_bytes(url, row.original_filename)
        except HandoffError:
            # The row exists only to carry this URL, and the usual reason the
            # fetch failed is that a presigned URL has expired. Leaving the row
            # behind would make it the oldest pending row on the next attempt, so
            # every retry would fail on it first and never reach the files that
            # are still fetchable -- leaving the caller no way to recover except
            # by hand-editing the database. Drop it so a re-registration starts
            # from a clean slate.
            session.delete(row)
            session.commit()
            raise
        stored_path, sha256, size = storage.save_bytes(content, row.original_filename)
        row.stored_path = stored_path
        row.sha256 = sha256
        row.size_bytes = size
        row.status = STATUS_STORED
        # Cleared once the bytes are ours: the signed URL has served its purpose
        # and should not linger in the database where it can be re-used.
        row.source_url = None
        session.commit()
    return len(rows)
