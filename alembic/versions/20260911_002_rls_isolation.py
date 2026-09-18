"""RLS isolation + secure auth helper functions.

Revision ID: 20260911_002
Revises: 20260911_001

Hand-written migration (Alembic autogenerate does NOT detect RLS). It enables and
forces row-level security on every tenanted table, creates the per-tenant
policies keyed off the ``app.current_association_id`` custom GUC, and provisions
the SECURITY DEFINER authentication helpers so the least-privilege app role can
perform an exact-email / exact-id credential lookup without ever listing rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260911_002"
down_revision = "20260911_001"
branch_labels = None
depends_on = None

# Tenanted tables whose RLS policy filters on association_id
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
    "edit_audit_log",
]


def upgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table}"
            f" USING (association_id = current_setting('app.current_association_id', true)::uuid)"
        )

    # associations is scoped on its own primary key `id`
    op.execute("ALTER TABLE associations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE associations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY association_isolation ON associations"
        " USING (id = current_setting('app.current_association_id', true)::uuid)"
    )

    # users is scoped on association_id. Pre-auth login requires reading a user
    # before the tenant context can be known, so authentication runs through
    # SECURITY DEFINER functions owned by the migration role (BYPASSRLS) that
    # return only the single exact match.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY user_association_isolation ON users"
        " USING (association_id = current_setting('app.current_association_id', true)::uuid)"
    )

    # The migration-owning role is allowed to bypass RLS so the SECURITY DEFINER
    # auth helpers can read the exact credential row pre-auth. The running app
    # never connects as `siroq`, so this does not weaken the app's isolation.
    op.execute("ALTER ROLE siroq BYPASSRLS")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth_find_user(p_email text)
        RETURNS TABLE(id uuid, association_id uuid, hashed_password text,
                      role text, full_name text, active boolean, pharmacy_id uuid)
        LANGUAGE sql SECURITY DEFINER AS $$
            SELECT id, association_id, hashed_password, role, full_name,
                   active, pharmacy_id
            FROM users WHERE email = p_email LIMIT 1;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION auth_find_user_by_id(p_id uuid)
        RETURNS TABLE(id uuid, association_id uuid, hashed_password text,
                      role text, full_name text, active boolean, pharmacy_id uuid)
        LANGUAGE sql SECURITY DEFINER AS $$
            SELECT id, association_id, hashed_password, role, full_name,
                   active, pharmacy_id
            FROM users WHERE id = p_id LIMIT 1;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_find_user(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION auth_find_user_by_id(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_find_user(text) TO siroq_app")
    op.execute("GRANT EXECUTE ON FUNCTION auth_find_user_by_id(uuid) TO siroq_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_find_user(text)")
    op.execute("DROP FUNCTION IF EXISTS auth_find_user_by_id(uuid)")

    for table in RLS_TABLES + ["associations", "users"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS association_isolation ON {table}")
        op.execute(f"DROP POLICY IF EXISTS user_association_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")