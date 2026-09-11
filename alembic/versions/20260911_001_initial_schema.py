"""initial_schema

Revision ID: 20260911_001
Revises: 
Create Date: 2026-09-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.sql.functions as sa_funcs

# revision identifiers, used by Alembic.
revision = "20260911_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgcrypto extension
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    # postgis extension
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    # associations table
    op.create_table(
        "associations",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("default_currency", sa.Char(3), nullable=False, server_default="USD"),
        sa.Column("default_timezone", sa.Text(), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # pharmacies table
    op.create_table(
        "pharmacies",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("external_code", sa.Text(), nullable=True),
        sa.Column("address_raw", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(9, 6), nullable=True),
        sa.Column("lng", sa.Numeric(9, 6), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa_true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # users table
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", sa.Uuid(), sa.ForeignKey("pharmacies.id"), nullable=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa_true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # applications table
    op.create_table(
        "applications",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", sa.Uuid(), sa.ForeignKey("pharmacies.id"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="'manual_upload'"),
        sa.Column("pharmacy_identifier_column", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="'active'"),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # mapping_profiles table
    op.create_table(
        "mapping_profiles",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.UniqueConstraint("application_id"), nullable=False),
        sa.Column("field_map", JSON, nullable=False, server_default="{}"),
        sa.Column("confidence_map", JSON, nullable=False, server_default="{}"),
        sa.Column("confirmed_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )

    # datasets table
    op.create_table(
        "datasets",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("bronze_file_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="'pending_mapping'"),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    # products table
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=True),
        sa.Column("form", sa.Text(), nullable=True),
        sa.Column("strength", sa.Text(), nullable=True),
        sa.Column("unit", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # batches table
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("lot_number", sa.Text(), nullable=False),
        sa.Column("expiry_date", sa.DateTime(), nullable=True),
        sa.Column("received_date", sa.DateTime(), nullable=True),
        sa.Column("supplier", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # inventory_events table
    op.create_table(
        "inventory_events",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", sa.Uuid(), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), sa.ForeignKey("batches.id"), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("event_timestamp", sa.DateTime(), nullable=False),
        sa.Column("extra_attributes", JSON, nullable=False, server_default="{}"),
    )

    # sales table
    op.create_table(
        "sales",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("pharmacy_id", sa.Uuid(), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.id"), nullable=False),
        sa.Column("sale_timestamp", sa.DateTime(), nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.Char(3), nullable=False, server_default="USD"),
        sa.Column("extra_attributes", JSON, nullable=False, server_default="{}"),
    )

    # sale_lines table
    op.create_table(
        "sale_lines",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("sale_id", sa.Uuid(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("prescriber_id", sa.Uuid(), sa.ForeignKey("prescribers.id"), nullable=True),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
    )

    # prescribers table
    op.create_table(
        "prescribers",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("specialty", sa.Text(), nullable=True),
        sa.Column("license_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
    )

    # edit_audit_log table
    op.create_table(
        "edit_audit_log",
        sa.Column("id", sa.Uuid(), server_default=sa_funcs.gen_random_uuid(), primary_key=True),
        sa.Column("association_id", sa.Uuid(), sa.ForeignKey("associations.id"), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("field", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("edited_at", sa.DateTime(), server_default=sa_funcs.now(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("edit_audit_log")
    op.drop_table("prescribers")
    op.drop_table("sale_lines")
    op.drop_table("sales")
    op.drop_table("inventory_events")
    op.drop_table("batches")
    op.drop_table("products")
    op.drop_table("datasets")
    op.drop_table("mapping_profiles")
    op.drop_table("applications")
    op.drop_table("users")
    op.drop_table("pharmacies")
    op.drop_table("associations")
    op.execute("DROP EXTENSION IF EXISTS postgis")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")