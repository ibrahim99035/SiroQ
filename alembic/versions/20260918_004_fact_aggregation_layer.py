"""Fact aggregation layer: daily sales, inventory, adherence, supplier snapshots.

Revision ID: 20260918_004
Revises: 20260918_003
Create Date: 2026-09-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.sql.functions as sa_funcs
from sqlalchemy.dialects.postgresql import UUID

gen_uuid = sa.func.gen_random_uuid()

revision = "20260918_004"
down_revision = "20260918_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Daily Sales Snapshots ---
    op.create_table(
        "daily_sales_snapshots",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", UUID(), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_revenue", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("transaction_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_units_sold", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("prescription_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("otc_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cash_revenue", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("insurance_revenue", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("gross_margin", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("net_margin", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("new_patient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refill_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_products_sold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
        sa.UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", name="uq_daily_sales_snapshot"),
    )

    # --- Daily Inventory Snapshots ---
    op.create_table(
        "daily_inventory_snapshots",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", UUID(), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_inventory_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_units_on_hand", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unique_products_in_stock", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units_near_expiry_30", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("value_near_expiry_30", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("units_near_expiry_60", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("value_near_expiry_60", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("units_near_expiry_90", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("value_near_expiry_90", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("dead_stock_units", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("dead_stock_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("waste_cost", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("turnover_rate", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("controlled_substance_variance", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
        sa.UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", name="uq_daily_inventory_snapshot"),
    )

    # --- Daily Adherence Snapshots ---
    op.create_table(
        "daily_adherence_snapshots",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", UUID(), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("patient_id", UUID(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("product_id", UUID(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("pdc_30", sa.Numeric(5, 4), nullable=True),
        sa.Column("pdc_90", sa.Numeric(5, 4), nullable=True),
        sa.Column("mpr_30", sa.Numeric(5, 4), nullable=True),
        sa.Column("mpr_90", sa.Numeric(5, 4), nullable=True),
        sa.Column("days_since_last_fill", sa.Integer(), nullable=True),
        sa.Column("refill_gap_days", sa.Integer(), nullable=True),
        sa.Column("is_adherent_80", sa.Boolean(), nullable=True),
        sa.Column("adherence_cohort", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
        sa.UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", "patient_id", "product_id", name="uq_daily_adherence_snapshot"),
    )

    # --- Daily Supplier Snapshots ---
    op.create_table(
        "daily_supplier_snapshots",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("snapshot_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supplier_id", UUID(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("total_ordered_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_received_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("fill_rate", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("on_time_delivery_rate", sa.Numeric(5, 2), nullable=False, server_default="0"),
        sa.Column("avg_lead_time_days", sa.Numeric(5, 2), nullable=True),
        sa.Column("open_po_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_po_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
        sa.UniqueConstraint("association_id", "snapshot_date", "supplier_id", name="uq_daily_supplier_snapshot"),
    )

    # RLS policies for new snapshot tables
    NEW_RLS_TABLES = [
        "daily_sales_snapshots",
        "daily_inventory_snapshots",
        "daily_adherence_snapshots",
        "daily_supplier_snapshots",
    ]
    for table in NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING (association_id = current_setting('app.current_association_id', true)::uuid)"
        )


def downgrade() -> None:
    for table in ["daily_supplier_snapshots", "daily_adherence_snapshots", "daily_inventory_snapshots", "daily_sales_snapshots"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.drop_table(table)