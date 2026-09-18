"""Pharmacy analytics models: Products fields, Batches qty_on_hand/location/cost_basis, Prescriptions, Patients, Payers, Suppliers, PurchaseOrders, AlertRules, Alerts.

Revision ID: 20260918_003
Revises: 20260911_002
Create Date: 2026-09-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.sql.functions as sa_funcs
from sqlalchemy.dialects.postgresql import JSONB, UUID

gen_uuid = sa.func.gen_random_uuid()

revision = "20260918_003"
down_revision = "20260911_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Products: add new columns ---
    op.add_column("products", sa.Column("ndc", sa.String(11), nullable=True, unique=True))
    op.add_column("products", sa.Column("generic_name", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("brand_name", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("therapeutic_class", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("controlled_schedule", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("cost_per_unit", sa.Numeric(12, 4), nullable=True))
    op.add_column("products", sa.Column("selling_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("products", sa.Column("reimbursement_rate", sa.Numeric(12, 2), nullable=True))
    op.add_column("products", sa.Column("shelf_life_days", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("storage_requirements", sa.Text(), nullable=True))

    # --- Batches: add new columns ---
    op.add_column("batches", sa.Column("quantity_on_hand", sa.Numeric(12, 3), nullable=False, server_default="0"))
    op.add_column("batches", sa.Column("location", sa.Text(), nullable=True))
    op.add_column("batches", sa.Column("cost_basis", sa.Numeric(12, 4), nullable=True))

    # --- Patients table ---
    op.create_table(
        "patients",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("patient_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("age_bracket", sa.Text(), nullable=True),
        sa.Column("gender", sa.String(1), nullable=True),
        sa.Column("chronic_conditions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("adherence_cohort", sa.Text(), nullable=True),
        sa.Column("loyalty_segment", sa.Text(), nullable=True),
        sa.Column("first_seen_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- Prescriptions table ---
    op.create_table(
        "prescriptions",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("patient_id", UUID(), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("prescriber_id", UUID(), sa.ForeignKey("prescribers.id"), nullable=True),
        sa.Column("product_id", UUID(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("rx_number", sa.Text(), nullable=False),
        sa.Column("original_rx_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("days_supply", sa.Integer(), nullable=False),
        sa.Column("refills_authorized", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refills_remaining", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prior_auth_status", sa.Text(), nullable=True),
        sa.Column("prior_auth_number", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- Payers table ---
    op.create_table(
        "payers",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("payer_name", sa.Text(), nullable=False),
        sa.Column("plan_name", sa.Text(), nullable=True),
        sa.Column("payer_type", sa.Text(), nullable=True),
        sa.Column("contract_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("dir_fee_rate", sa.Numeric(12, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- Suppliers table ---
    op.create_table(
        "suppliers",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("supplier_name", sa.Text(), nullable=False),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("contact_phone", sa.Text(), nullable=True),
        sa.Column("lead_time_days", sa.Integer(), nullable=True),
        sa.Column("fill_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("is_340b", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- PurchaseOrders table ---
    op.create_table(
        "purchase_orders",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("supplier_id", UUID(), sa.ForeignKey("suppliers.id"), nullable=False),
        sa.Column("product_id", UUID(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("po_number", sa.Text(), nullable=False),
        sa.Column("ordered_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("received_quantity", sa.Numeric(12, 3), nullable=False, server_default="0"),
        sa.Column("unit_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("order_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- AlertRules table ---
    op.create_table(
        "alert_rules",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("module", sa.String(30), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("condition", sa.String(20), nullable=False),
        sa.Column("threshold_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Alerts table ---
    op.create_table(
        "alerts",
        sa.Column("id", UUID(), server_default=gen_uuid, primary_key=True),
        sa.Column("association_id", UUID(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("rule_id", UUID(), sa.ForeignKey("alert_rules.id"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", UUID(), nullable=False),
        sa.Column("metric_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("threshold_value", sa.Numeric(12, 4), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("recommended_action", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("acknowledged_by", UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa_funcs.now(), nullable=False),
    )

    # --- Add RLS policies for new tenanted tables ---
    NEW_RLS_TABLES = [
        "patients",
        "prescriptions",
        "payers",
        "suppliers",
        "purchase_orders",
        "alert_rules",
        "alerts",
    ]
    for table in NEW_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING (association_id = current_setting('app.current_association_id', true)::uuid)"
        )


def downgrade() -> None:
    # Drop RLS policies
    for table in ["alerts", "alert_rules", "purchase_orders", "suppliers", "payers", "prescriptions", "patients"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Drop tables in reverse order
    op.drop_table("alerts")
    op.drop_table("alert_rules")
    op.drop_table("purchase_orders")
    op.drop_table("suppliers")
    op.drop_table("payers")
    op.drop_table("prescriptions")
    op.drop_table("patients")

    # Drop columns from batches
    op.drop_column("batches", "cost_basis")
    op.drop_column("batches", "location")
    op.drop_column("batches", "quantity_on_hand")

    # Drop columns from products
    op.drop_column("products", "storage_requirements")
    op.drop_column("products", "shelf_life_days")
    op.drop_column("products", "reimbursement_rate")
    op.drop_column("products", "selling_price")
    op.drop_column("products", "cost_per_unit")
    op.drop_column("products", "controlled_schedule")
    op.drop_column("products", "therapeutic_class")
    op.drop_column("products", "brand_name")
    op.drop_column("products", "generic_name")
    op.drop_column("products", "ndc")