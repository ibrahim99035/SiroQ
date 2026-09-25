"""Pytest fixtures.

The suite runs against a dedicated ``*_test`` database, never the development
database: the isolation fixture truncates tables, so pointing it at the dev
database would silently erase real applications, files and analyses. The
override below must happen before anything imports ``app.config``.
"""
import os

import pytest

_TEST_DB_SUFFIX = "_test"


def _redirect_to_test_database() -> None:
    import sqlalchemy
    from sqlalchemy.engine import make_url

    url = make_url(os.environ.get("MIGRATIONS_DATABASE_URL") or
                   "postgresql+psycopg://siroq:siroq_dev_password@localhost:5433/siroq")
    if (url.database or "").endswith(_TEST_DB_SUFFIX):
        return

    test_url = url.set(database=(url.database or "siroq") + _TEST_DB_SUFFIX)
    maintenance_url = url.set(database="postgres")

    # The API under test connects as a least-privilege role, and
    # docker/init-db grants that role privileges on the *development* database
    # only. A freshly created test database therefore starts with no
    # CONNECT/USAGE and no table grants, which fails every API test on CI. Grant
    # the same default privileges here that the init script sets up, so the
    # tables this run creates are reachable by the app role.
    app_role = (make_url(os.environ.get("DATABASE_URL") or url).username
                or "siroq_app")

    engine = sqlalchemy.create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            exists = conn.execute(
                sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": test_url.database},
            ).scalar()
            if not exists:
                conn.execute(sqlalchemy.text(f'CREATE DATABASE "{test_url.database}"'))
            conn.execute(sqlalchemy.text(
                f'GRANT CONNECT ON DATABASE "{test_url.database}" TO "{app_role}"'))
    finally:
        engine.dispose()

    # Schema-level and default-privilege grants are per-database, so they must
    # be issued from inside the test database — not from the maintenance one.
    # ON ALL TABLES also covers a test database that already had tables.
    test_engine = sqlalchemy.create_engine(test_url, isolation_level="AUTOCOMMIT")
    try:
        with test_engine.connect() as conn:
            for stmt in (
                f'GRANT USAGE ON SCHEMA public TO "{app_role}"',
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{url.username}" IN SCHEMA public '
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_role}"',
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{url.username}" IN SCHEMA public '
                f'GRANT USAGE, SELECT ON SEQUENCES TO "{app_role}"',
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public '
                f'TO "{app_role}"',
                f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_role}"',
            ):
                conn.execute(sqlalchemy.text(stmt))
    finally:
        test_engine.dispose()

    for var in ("DATABASE_URL", "MIGRATIONS_DATABASE_URL"):
        env_url = make_url(os.environ.get(var) or url)
        os.environ[var] = env_url.set(database=test_url.database).render_as_string(
            hide_password=False
        )


_redirect_to_test_database()

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.analytics_service.storage import delete_tree  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

# Schema setup (create/truncate) needs the migration-owner role; the running
# app connects as the least-privilege role which the API itself uses.
migration_engine = create_engine(settings.MIGRATIONS_DATABASE_URL)


def api_headers() -> dict:
    return {"X-API-Key": settings.API_KEY}


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=migration_engine)
    yield


@pytest.fixture(autouse=True)
def _clean_state(_schema):
    """Isolate every test: empty the storage root and the three tables."""
    delete_tree()
    with migration_engine.connect() as conn:
        conn.execute(text("TRUNCATE analyses, files, applications CASCADE"))
        conn.commit()
    yield


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c