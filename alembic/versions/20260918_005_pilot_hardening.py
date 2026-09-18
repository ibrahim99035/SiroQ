"""Pilot hardening: upload idempotency, transaction grouping, RLS null-safety.

Revision ID: 20260918_005
Revises: 20260918_004

Three Phase-1 correctness fixes that all need schema/DDL changes:

1. ``datasets.content_hash`` — lets the upload path refuse a byte-identical file
   that was already committed, which previously doubled reported revenue.
2. ``sales.transaction_ref`` — a receipt/transaction key so a POS export whose
   lines are one row each is counted as one transaction, not N.
3. RLS hardening: every existing policy is rebuilt so an *unset* tenant variable
   yields no rows instead of a cast error. ``current_setting(..., true)`` returns
   the empty string (not NULL) on a pooled connection whose transaction-local
   ``set_config`` has ended, and ``''::uuid`` used to raise ``DataError``. The
   policies now use ``nullif(..., '')::uuid`` so isolation fails *closed*.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260918_005"
down_revision = "20260918_004"
branch_labels = None
depends_on = None

# Every tenanted table whose policy filters on association_id
TENANT_TABLES = [
    "pharmacies", "applications", "mapping_profiles", "datasets", "products",
    "batches", "inventory_events", "sales", "sale_lines", "prescribers",
    "edit_audit_log", "patients", "prescriptions", "payers", "suppliers",
    "purchase_orders", "alert_rules", "alerts", "daily_sales_snapshots",
    "daily_inventory_snapshots", "daily_adherence_snapshots",
    "daily_supplier_snapshots",
]

SAFE_EXPR = "nullif(current_setting('app.current_association_id', true), '')::uuid"
LEGACY_EXPR = "current_setting('app.current_association_id', true)::uuid"


def _rebuild_policies(expr: str) -> None:
    """Recreate every RLS policy against ``expr`` (drop-then-create is the only
    way to change a policy's USING clause in place)."""
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING (association_id = {expr})"
        )

    op.execute("DROP POLICY IF EXISTS association_isolation ON associations")
    op.execute(
        f"CREATE POLICY association_isolation ON associations USING (id = {expr})"
    )

    op.execute("DROP POLICY IF EXISTS user_association_isolation ON users")
    op.execute(
        f"CREATE POLICY user_association_isolation ON users"
        f" USING (association_id = {expr})"
    )


def upgrade() -> None:
    op.add_column("datasets", sa.Column("content_hash", sa.String(64), nullable=True))
    op.create_index("ix_datasets_application_content_hash", "datasets",
                    ["application_id", "content_hash"])

    op.add_column("sales", sa.Column("transaction_ref", sa.String(64), nullable=True))
    op.create_index("ix_sales_transaction_ref", "sales", ["transaction_ref"])

    _rebuild_policies(SAFE_EXPR)


def downgrade() -> None:
    _rebuild_policies(LEGACY_EXPR)

    op.drop_index("ix_sales_transaction_ref", table_name="sales")
    op.drop_column("sales", "transaction_ref")

    op.drop_index("ix_datasets_application_content_hash", table_name="datasets")
    op.drop_column("datasets", "content_hash")