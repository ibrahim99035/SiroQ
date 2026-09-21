"""analysis-service: enforce unique application names

Revision ID: 20260921_007
Revises: 20260921_006
Create Date: 2026-09-21

Application names are the idempotency key for the one-shot upload path
(``POST /api/v1/analyze`` with the same ``application_name`` must keep
returning the same application). Enforce that at the schema level so a
concurrent create-race cannot fabricate a duplicate.
"""
from alembic import op

revision = "20260921_007"
down_revision = "20260921_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_applications_name", "applications", ["name"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_applications_name", table_name="applications")