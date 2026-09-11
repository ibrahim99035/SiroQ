"""RLS isolation

Revision ID: 20260911_002
Revises: 20260911_001
Create Date: 2026-09-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260911_002"
down_revision = "20260911_001"
branch_labels = None
depends_on = None


# Tables that need RLS policies (from Section 7.5)
RLS_TABLES = [
    "pharmacies",
    "applications",
    "mapping_profiles",
    "datasets",
    "products",
    "batches",
    "inventory_events",
    "sales",
    "sale_lines",
    "prescribers",
]

NO_RLS_TABLES = ["associations", "users"]


def upgrade() -> None:
    # Enable RLS on all tenanted tables
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # Create RLS policies using the application.current_association_id session variable
    for table in RLS_TABLES:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING (association_id = current_setting('app.current_association_id', true)::uuid)"
        )

    # Handle associations - use id instead of association_id
    op.execute(
        "ALTER TABLE associations ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE associations FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY association_isolation ON associations"
        " USING (id = current_setting('app.current_association_id', true)::uuid)"
    )

    # Handle users - also use association_id
    op.execute(
        "ALTER TABLE users ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE users FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY user_association_isolation ON users"
        " USING (association_id = current_setting('app.current_association_id', true)::uuid)"
    )


def downgrade() -> None:
    # Drop RLS policies and disable RLS
    for table in RLS_TABLES + NO_RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS association_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS user_association_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")