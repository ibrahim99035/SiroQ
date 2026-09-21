import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.analytics_service.storage import delete_tree
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.main import app

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