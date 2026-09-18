"""Live Row-Level-Security verification.

Connects as the least-privilege ``siroq_app`` role (never ``siroq``) so a
table-owner bypass cannot fake a pass. Inserts two pharmacy rows under two
different fake association ids, then asserts that switching the tenant
variable shows ONLY the matching row. Also verifies the RLS flags, policy
existence and that ``siroq`` (not ``siroq_app``) owns every table.
"""
import pytest
from sqlalchemy import create_engine, text

from app.config import settings

A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

TENANT_TABLES = [
    "associations", "pharmacies", "users", "applications", "mapping_profiles",
    "datasets", "products", "batches", "inventory_events", "sales",
    "sale_lines", "prescribers", "edit_audit_log",
]


def _app_engine():
    return create_engine(settings.DATABASE_URL, future=True)  # siroq_app


def _owner_engine():
    return create_engine(settings.MIGRATIONS_DATABASE_URL, future=True)  # siroq


def _set_context(conn, assoc_id):
    conn.execute(text("SELECT set_config('app.current_association_id', :a, true)"),
                 {"a": str(assoc_id)})


def _insert_pharmacy(engine, assoc_id, name):
    with engine.begin() as conn:
        _set_context(conn, assoc_id)
        conn.execute(
            text("INSERT INTO pharmacies(association_id, name) VALUES (:a, :n)"),
            {"a": assoc_id, "n": name},
        )


def _visible_pharmacy_names(engine, assoc_id, names):
    with engine.begin() as conn:
        _set_context(conn, assoc_id)
        rows = conn.execute(text("SELECT name FROM pharmacies")).fetchall()
    return [r[0] for r in rows if r[0] in names]


def test_unset_tenant_context_returns_no_rows_not_error():
    """Regression: an empty tenant variable used to raise a uuid cast error
    instead of simply hiding every row. Isolation must fail *closed*."""
    eng = _app_engine()
    with eng.begin() as conn:
        # the empty string a finished transaction-local set_config leaves behind
        conn.execute(text("SELECT set_config('app.current_association_id', '', true)"))
        n = conn.execute(text("SELECT count(*) FROM pharmacies")).scalar()
    assert n == 0


def test_rls_flags_and_flavor():
    eng = _owner_engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT tablename, rowsecurity, tableowner "
            "FROM pg_tables WHERE schemaname='public'"
        )).fetchall()
    by = {r[0]: r for r in rows}
    for t in TENANT_TABLES:
        assert t in by, f"{t} missing from pg_tables"
        assert by[t][1], f"{t} rowsecurity is off"
    # siroq (not the app role) must own every table
    assert all(r[2] == "siroq" for r in rows), f"non-siroq owner: {[(r[0], r[2]) for r in rows]}"


def test_tenant_isolation_policy_exists():
    eng = _owner_engine()
    with eng.connect() as conn:
        n = conn.execute(text(
            "SELECT count(*) FROM pg_policies "
            "WHERE tablename='pharmacies' AND policyname='tenant_isolation'"
        )).scalar()
    assert n == 1, "tenant_isolation policy missing on pharmacies"


def test_cross_tenant_isolation():
    eng = _app_engine()
    owner_eng = _owner_engine()
    
    # Create test associations using owner role (bypasses RLS)
    with owner_eng.begin() as conn:
        conn.execute(text("INSERT INTO associations (id, name) VALUES (CAST(:a AS uuid), 'RLS-Alpha'), (CAST(:b AS uuid), 'RLS-Beta') ON CONFLICT DO NOTHING"),
                     {"a": A, "b": B})
    
    # clean any leftovers from a previous run
    for assoc, name in ((A, "RLS-Alpha"), (B, "RLS-Beta")):
        with eng.begin() as conn:
            _set_context(conn, assoc)
            conn.execute(text("DELETE FROM pharmacies WHERE name=:n"), {"n": name})

    _insert_pharmacy(eng, A, "RLS-Alpha")
    _insert_pharmacy(eng, B, "RLS-Beta")

    visible_a = _visible_pharmacy_names(eng, A, ["RLS-Alpha", "RLS-Beta"])
    assert visible_a == ["RLS-Alpha"], f"tenant A saw: {visible_a}"

    visible_b = _visible_pharmacy_names(eng, B, ["RLS-Alpha", "RLS-Beta"])
    assert visible_b == ["RLS-Beta"], f"tenant B saw: {visible_b}"

    # cleanup
    for assoc, name in ((A, "RLS-Alpha"), (B, "RLS-Beta")):
        with eng.begin() as conn:
            _set_context(conn, assoc)
            conn.execute(text("DELETE FROM pharmacies WHERE name=:n"), {"n": name})
    
    # cleanup associations
    with owner_eng.begin() as conn:
        conn.execute(text("DELETE FROM associations WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": A, "b": B})