"""Raw-file persistence. Files are stored byte-for-byte under the configured
storage root, keyed by their sha256 (never overwritten, never edited).
"""
from pathlib import Path

from app.config import settings


def ensure_root() -> Path:
    root = Path(settings.STORAGE_PATH)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_bytes(content: bytes, original_filename: str) -> tuple[str, str, int]:
    """Save raw bytes. Returns ``(relative_path, sha256, size_bytes)``.

    A file is stored as ``<root>/<first2>/<sha256>.<ext>``. Re-uploading the
    same bytes resolves to the same path, so repeat uploads are idempotent.
    """
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    ext = Path(original_filename).suffix.lstrip(".").lower() or "bin"
    dest = ensure_root() / digest[:2] / f"{digest}.{ext}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    return str(dest.relative_to(ensure_root())), digest, len(content)


def read_bytes(rel_path: str) -> bytes:
    dest = ensure_root() / rel_path
    return dest.read_bytes()


def delete_tree() -> None:
    """Remove everything under the storage root (used by tests)."""
    import shutil

    root = ensure_root()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)