"""analysis queue state: started_at + drop orphaned legacy auth helpers

Revision ID: 20261003_008
Revises: 20260921_007
Create Date: 2026-10-03 00:00:00.000000

Adds ``analyses.started_at`` so the background worker can tell a job that is
genuinely in flight from one whose process was killed mid-run. Without it a
restarting worker has no age signal and can only guess.

Also drops the two ``auth_find_user*`` SECURITY DEFINER helpers. Revision 006
dropped the ``users`` table with CASCADE, but Postgres does not record a
dependency from a ``LANGUAGE sql`` function written with a string body to the
tables it selects from, so the functions outlived their table and were left in
the database pointing at an object that no longer exists. Nothing in the v1
service schema calls them.
"""
from alembic import op
import sqlalchemy as sa

revision = "20261003_008"
down_revision = "20260921_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Idempotent: these are already absent on databases created after 006 was
    # squashed onto a fresh instance, and present everywhere 006 ran.
    op.execute("DROP FUNCTION IF EXISTS auth_find_user(text)")
    op.execute("DROP FUNCTION IF EXISTS auth_find_user_by_id(uuid)")


def downgrade() -> None:
    op.drop_column("analyses", "started_at")
