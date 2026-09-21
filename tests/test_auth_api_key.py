"""Auth: static API key required everywhere except /health."""
from io import BytesIO

from tests.conftest import api_headers

CSV = "sale_timestamp,transaction_ref,product_name,quantity,total_amount\n"


def test_health_is_open(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_missing_key_rejected(client):
    assert client.get("/api/v1/applications/any").status_code == 401
    assert (
        client.post(
            "/api/v1/analyze",
            data={"application_name": "x"},
            files=[("files", ("a.csv", BytesIO(CSV.encode()), "text/csv"))],
        ).status_code
        == 401
    )


def test_wrong_key_rejected(client):
    assert (
        client.get(
            "/api/v1/applications/any", headers={"X-API-Key": "bogus"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "bogus"},
            data={"application_name": "x"},
            files=[("files", ("a.csv", BytesIO(CSV.encode()), "text/csv"))],
        ).status_code
        == 401
    )


def test_valid_key_accepted(client):
    assert (
        client.get("/api/v1/applications/any", headers=api_headers()).status_code
        == 404
    )