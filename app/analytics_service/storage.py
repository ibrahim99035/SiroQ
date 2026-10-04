"""Raw-file persistence. Files are stored byte-for-byte, keyed by their sha256
(never overwritten, never edited), through one of two drivers:

* ``local`` — a directory on this machine. Used by development, CI and the test
  suite, where a throwaway directory is cheaper than a network round trip.
* ``s3``    — any S3-compatible object store. Required in production: a Render
  service has an ephemeral filesystem, so a local root is lost on every redeploy
  and every scale-out while the ``files`` table still points at it.

Both drivers keep the same on-disk/on-object layout — ``<prefix><2 hex>/<sha256>.<ext>``
— so a row written by one driver is readable by the other and the extension stays
visible in the key (some readers dispatch on it).
"""
import hashlib
import logging
import mimetypes
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# A handful of extensions the pipeline accepts. mimetypes does not always know
# the legacy Excel ones, and a wrong Content-Type makes presigned downloads
# arrive as an octet-stream download instead of opening in a spreadsheet.
_EXTRA_MIME_TYPES = {
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".csv": "text/csv",
}

_client = None


def _s3_client():
    """Build the S3 client once, on first use.

    Imported lazily so a ``local`` deployment never needs boto3 installed —
    that keeps the offline dev and CI paths free of a network SDK.
    """
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        _client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            config=Config(
                # Neon Object Storage is path-style only. Virtual-hosted-style
                # addressing there returns 400, so this follows S3_FORCE_PATH_STYLE
                # rather than being hard-coded on.
                s3={"addressing_style": "path" if settings.S3_FORCE_PATH_STYLE else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
    return _client


def _content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return _EXTRA_MIME_TYPES.get(suffix) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _key_for(rel_path: str) -> str:
    """Prefix a stored relative path with the configured key namespace."""
    prefix = settings.STORAGE_KEY_PREFIX.strip("/")
    return f"{prefix}/{rel_path}" if prefix else rel_path


def ensure_root() -> Path:
    """Return the local storage root, creating it if needed.

    Local driver only. Kept because the local driver and the test suite rely on
    it; the S3 driver has no equivalent concept.
    """
    root = Path(settings.STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _local_path(rel_path: str) -> Path:
    """Resolve a stored relative path, refusing anything outside the root.

    ``stored_path`` only ever comes from this module, but it is persisted in a
    column that a migration or an admin script could touch, and a ``..``
    segment would otherwise turn a read into arbitrary file access.
    """
    root = ensure_root().resolve()
    dest = (root / rel_path).resolve()
    if dest != root and root not in dest.parents:
        raise ValueError(f"Refusing to read outside the storage root: {rel_path!r}")
    return dest


def save_bytes(content: bytes, original_filename: str) -> tuple[str, str, int]:
    """Save raw bytes. Returns ``(rel_path, sha256, size_bytes)``.

    A file is stored as ``<prefix><first2>/<sha256>.<ext>``. Re-uploading the
    same bytes resolves to the same path, so repeat uploads are idempotent.
    """
    digest = hashlib.sha256(content).hexdigest()
    ext = Path(original_filename).suffix.lstrip(".").lower() or "bin"
    rel_path = f"{digest[:2]}/{digest}.{ext}"

    if settings.STORAGE_DRIVER == "s3":
        client = _s3_client()
        key = _key_for(rel_path)
        # Copy-on-write storage means the object is already immutable, but
        # skipping the round trip keeps a duplicate re-upload free.
        try:
            client.head_object(Bucket=settings.S3_BUCKET, Key=key)
            return rel_path, digest, len(content)
        except Exception:  # noqa: BLE001 - absence is the normal case here
            logger.debug("head_object miss for %s; uploading", key)

        client.put_object(
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=content,
            ContentType=_content_type(original_filename),
            Metadata={"sha256": digest},
        )
        return rel_path, digest, len(content)

    dest = _local_path(rel_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return rel_path, digest, len(content)


def read_bytes(rel_path: str) -> bytes:
    if settings.STORAGE_DRIVER == "s3":
        obj = _s3_client().get_object(Bucket=settings.S3_BUCKET, Key=_key_for(rel_path))
        return obj["Body"].read()

    return _local_path(rel_path).read_bytes()


def delete_tree() -> None:
    """Remove everything this service owns in storage.

    Local driver: the storage root. S3 driver: every object under the key
    prefix, so a shared bucket keeps the client's own objects untouched. Used
    by the test suite to isolate runs; not wired to any request path.

    The S3 branch is refused. Tests isolate themselves by wiping storage, and
    nothing else calls this — so if it ever runs while the driver is ``s3`` it
    is a test run pointed at the production bucket, and the materialised files
    that ``preview``/``series``/``forecast`` read are not recoverable: there is
    no retention or GC anywhere in this service. ``tests/conftest.py`` pins the
    driver to ``local`` before importing ``app.config``; this is the backstop
    for when that is bypassed.
    """
    if settings.STORAGE_DRIVER != "local":
        raise RuntimeError(
            f"delete_tree() refuses to run with STORAGE_DRIVER="
            f"{settings.STORAGE_DRIVER!r}: it would delete production objects."
        )

    root = ensure_root()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
