"""Shared pytest fixtures for SiroQ.

Tests run inside the app container (``docker compose exec app pytest -v``)
where ``DATABASE_URL`` points at the least-privilege ``siroq_app`` role and the
schema has already been migrated/seeded via ``make migrate`` / ``make seed``.
"""
import sys
from pathlib import Path
import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.seed import idempotent_create


@pytest.fixture(scope="session")
def seeded_db():
    idempotent_create()
    return True


@pytest.fixture(scope="session")
def client(seeded_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


def login(client, email, password="ChangeMe123!"):
    return client.post(
        "/login", data={"email": email, "password": password},
        follow_redirects=False,
    )


@pytest.fixture
def admin_session(client):
    login(client, "admin@siroq.local")
    yield client
    client.post("/logout")


@pytest.fixture
def viewer_session(client):
    login(client, "viewer@siroq.local")
    yield client
    client.post("/logout")


@pytest.fixture
def datasteward_session(client):
    login(client, "steward@siroq.local")
    yield client
    client.post("/logout")