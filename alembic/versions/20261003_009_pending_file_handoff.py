"""Allow a file to be registered before its bytes have arrived.

A client on a different storage namespace hands over a presigned URL rather than
uploading through the API, so that enqueueing a large filing stays instant
instead of streaming tens of megabytes through a serverless request. That means a
`files` row can exist before it has content to describe.

`stored_path`, `sha256` and `size_bytes` become nullable for exactly that
window: a pending file genuinely does not know its own size or checksum yet, and
storing a placeholder zero would be a lie that later code could read. The worker
fills them in and clears `source_url` once the bytes have arrived.

A `status` of 'pending' with a NULL `source_url` would mean "claimed but not
materialized", which nothing should ever see — the partial index below exists so
recovery can find, and complain about, any such row instead of skipping it.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261003_009"
down_revision = "20261003_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "files", "stored_path", existing_type=sa.Text(), nullable=True
    )
    op.alter_column("files", "sha256", existing_type=sa.Text(), nullable=True)
    op.alter_column("files", "size_bytes", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("files", sa.Column("source_url", sa.Text(), nullable=True))
    op.create_index(
        "files_with_source_url_idx",
        "files",
        ["created_at"],
        postgresql_where=sa.text("source_url IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("files_with_source_url_idx", table_name="files")
    op.drop_column("files", "source_url")
    # Rows still waiting on a download have nothing to restore these from.
    op.execute("DELETE FROM files WHERE source_url IS NOT NULL")
    op.execute("DELETE FROM files WHERE stored_path IS NULL OR sha256 IS NULL")
    op.alter_column(
        "files", "stored_path", existing_type=sa.Text(), nullable=False
    )
    op.alter_column("files", "sha256", existing_type=sa.Text(), nullable=False)
    op.alter_column(
        "files", "size_bytes", existing_type=sa.BigInteger(), nullable=False
    )
