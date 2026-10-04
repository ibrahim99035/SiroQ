"""Pytest fixtures.

The suite runs against a dedicated ``*_test`` database, never the development
database: the isolation fixture truncates tables, so pointing it at the dev
database would silently erase real applications, files and analyses. The
override below must happen before anything imports ``app.config``.

The schema is built by running the real Alembic migrations rather than
``Base.metadata.create_all``. ``create_all`` reads the models, so it can only
ever prove the models agree with themselves; it cannot catch a migration that
forgot a column, and on a developer's machine — where the test database outlives
the change — it leaves a stale schema that fails every test with "column does not
exist" until dropped by hand. Migrations are the thing that actually gets
deployed, so they are the thing worth testing.

There is no local database to fall back to any more; the database is Neon, and
``MIGRATIONS_DATABASE_URL`` is required.
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEST_DB_SUFFIX = "_test"

# `make test` is a bare `pytest`, and pytest never reads .env — only
# `app.config` does, and that is imported further down, after this module has
# already needed the connection strings. So load .env here, explicitly, into
# os.environ. Real environment variables still win: load_dotenv does not
# override them, which is what lets CI pass both URLs in the workflow.
load_dotenv(_REPO_ROOT / ".env")

# The isolation fixture calls ``delete_tree()``, which under ``STORAGE_DRIVER=s3``
# deletes every object under ``STORAGE_KEY_PREFIX`` — that is the production
# bucket. Pin the local driver (and a scratch path) here, after .env is loaded and
# before anything imports ``app.config``, so that a developer ``.env`` carrying
# production storage credentials cannot turn a test run into data loss. Deployment
# requires ``STORAGE_DRIVER=s3``, so both values are set in the same environments.
os.environ["STORAGE_DRIVER"] = "local"
os.environ["STORAGE_PATH"] = "/tmp/siroq_test_storage"

#: Set by _redirect_to_test_database; reused by the schema fixture below.
_TEST_DB = {"url": None, "app_role": None, "owner": None}


def _apply_grants(test_url, app_role: str, owner: str) -> None:
    """Make the tables Alembic is about to create reachable by the app role.

    ``docker/init-db`` grants these on the development database only, and the
    privileges are per-database, so a fresh test database starts with no
    CONNECT/USAGE and no table grants — which fails every API test on CI.
    Default privileges cover tables created after this point; ON ALL TABLES
    covers any that already exist.
    """
    import sqlalchemy

    test_engine = sqlalchemy.create_engine(
        test_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"options": "-c lock_timeout=15s -c statement_timeout=300s"},
    )
    try:
        with test_engine.connect() as conn:
            for stmt in (
                f'GRANT CONNECT ON DATABASE "{test_url.database}" TO "{app_role}"',
                f'GRANT USAGE ON SCHEMA public TO "{app_role}"',
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{app_role}"',
                f'ALTER DEFAULT PRIVILEGES FOR ROLE "{owner}" IN SCHEMA public '
                f'GRANT USAGE, SELECT ON SEQUENCES TO "{app_role}"',
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public '
                f'TO "{app_role}"',
                f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{app_role}"',
            ):
                conn.execute(sqlalchemy.text(stmt))
    finally:
        test_engine.dispose()


def _redirect_to_test_database() -> None:
    import sqlalchemy
    from sqlalchemy.engine import make_url

    raw = os.environ.get("MIGRATIONS_DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "MIGRATIONS_DATABASE_URL must be set. This suite has no local "
            "database to fall back to; see .env.example for the Neon shape."
        )
    url = make_url(raw)

    if (url.database or "").endswith(_TEST_DB_SUFFIX):
        test_url = url
    else:
        # `CREATE DATABASE` cannot run inside a transaction block, hence
        # AUTOCOMMIT. It runs on the connection we were handed rather than
        # through a "postgres" maintenance database: a Neon branch holds
        # `neondb` and no `postgres`, so the old hop fails there. This URL is
        # MIGRATIONS_DATABASE_URL, which is the direct (non-pooler) endpoint —
        # DDL must not sit inside a pooled transaction.
        test_db = (url.database or "neondb") + _TEST_DB_SUFFIX
        engine = sqlalchemy.create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as conn:
                exists = conn.execute(
                    sqlalchemy.text("SELECT 1 FROM pg_database WHERE datname = :n"),
                    {"n": test_db},
                ).scalar()
                if not exists:
                    conn.execute(
                        sqlalchemy.text(f'CREATE DATABASE "{test_db}"')
                    )
        finally:
            engine.dispose()
        test_url = url.set(database=test_db)

    app_role = (make_url(os.environ.get("DATABASE_URL") or url).username
                or "siroq_app")

    _apply_grants(test_url, app_role, url.username)
    _TEST_DB.update(url=test_url, app_role=app_role, owner=url.username)

    for var in ("DATABASE_URL", "MIGRATIONS_DATABASE_URL"):
        env_url = make_url(os.environ.get(var) or url)
        os.environ[var] = env_url.set(database=test_url.database).render_as_string(
            hide_password=False
        )


_redirect_to_test_database()

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

from app.analytics_service.storage import delete_tree  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

# Schema setup (reset, migrate, truncate) needs the migration-owner role; the
# running app connects as the least-privilege role which the API itself uses.
migration_engine = create_engine(
    settings.MIGRATIONS_DATABASE_URL,
    connect_args={"options": "-c lock_timeout=15s -c statement_timeout=300s"},
)

# Neon leaves lock_timeout/statement_timeout at 0, i.e. wait forever. A TRUNCATE
# that contends for a lock then wedges the whole run with no output, which is
# indistinguishable from a slow test. Bound them.
_TIMEOUT_OPTS = {"options": "-c lock_timeout=15s -c statement_timeout=300s"}


def _reset_and_migrate() -> None:
    """Rebuild the test database from the real migrations.

    The test database is disposable by definition, so it is rebuilt rather than
    patched: that makes every run start from the exact schema that will be
    deployed, and makes a stale local database impossible rather than merely
    unlikely.
    """
    with migration_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    # Recreating the schema threw away its grants, and the app role connects
    # through a pool whose cached statements still reference the dropped tables.
    _apply_grants(_TEST_DB["url"], _TEST_DB["app_role"], _TEST_DB["owner"])
    engine.dispose()

    config = AlembicConfig(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    command.upgrade(config, "head")


def api_headers() -> dict:
    return {"X-API-Key": settings.API_KEY}


@pytest.fixture(scope="session", autouse=True)
def _schema():
    _reset_and_migrate()
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