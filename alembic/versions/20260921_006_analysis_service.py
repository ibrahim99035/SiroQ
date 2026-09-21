"""analysis-service v1: drop legacy tenancy schema, create the flat service tables

Revision ID: 20260921_006
Revises: 20260918_005
Create Date: 2026-09-21

This migration pivots the database to the standalone analysis service: the
tenancy/RLS schema (associations, pharmacies, users, RLS-protected fact tables)
is dropped and replaced by flat applications / files / analyses tables.

The migration-owner role (``siroq``) creates the tables; the running application
role (``siroq_app``) receives privileges automatically via the default
privileges established at database init.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260921_006"
down_revision = "20260918_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    legacy_tables = [
        "associations", "pharmacies", "users", "applications", "mapping_profiles",
        "datasets", "products", "batches", "inventory_events", "sales",
        "sale_lines", "prescribers", "patients", "prescriptions", "payers",
        "suppliers", "purchase_orders", "edit_audit_log", "alert_rules",
        "alerts", "daily_sales_snapshots", "daily_inventory_snapshots",
        "daily_adherence_snapshots", "daily_supplier_snapshots",
    ]
    for table in legacy_tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS user_role_enum")

    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="stored"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_files_application_id", "files", ["application_id"])

    op.create_table(
        "analyses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "application_id",
            sa.Uuid(),
            sa.ForeignKey("applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default="completed"
        ),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analyses_application_id", "analyses", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_analyses_application_id", table_name="analyses")
    op.drop_table("analyses")
    op.drop_index("ix_files_application_id", table_name="files")
    op.drop_table("files")
    op.drop_table("applications")