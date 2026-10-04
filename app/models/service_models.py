from datetime import datetime
import uuid

from sqlalchemy import Uuid, Text, BigInteger, JSON, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Application(Base):
    """A flat, top-level grouping for a batch of related files."""

    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class StoredFile(Base):
    """A raw uploaded file, stored byte-for-byte on disk and never edited."""

    __tablename__ = "files"

    id: Mapped[str] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable only for a file registered from a handed-off URL whose bytes have
    # not been fetched yet. See migration 20261003_009 and
    # app/analytics_service/handoff.py: the worker fills these in.
    stored_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="stored")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the bytes still are, while `status` is 'pending'. Cleared by the
    # worker once the bytes are in our own storage.
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Analysis(Base):
    """One saved deep-analysis run over an application's files."""

    __tablename__ = "analyses"

    id: Mapped[str] = mapped_column(Uuid(), primary_key=True, default=_uuid)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="completed")
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )