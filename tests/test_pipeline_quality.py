"""Pipeline correctness against messy input: nulls, dupes, bad numerics."""
from io import BytesIO

from tests.conftest import api_headers

MESSY_CSV = """sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,note
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,ok
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,ok
2026-09-02,,Ibuprofen 400mg,,not-a-number,20.00,
2026-09-03,TX-003,Paracetamol 500mg,1,12.50,-37.50,bad sign
2026-09-04,TX-004,,3,,,
"""


def _messy(client, name="messy-app"):
    return client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files=[("files", ("messy.csv", BytesIO(MESSY_CSV.encode()), "text/csv"))],
    )


def test_messy_csv_still_enriched(client):
    r = _messy(client)
    assert r.status_code == 200, r.text
    f = r.json()["report"]["files"][0]
    assert f["top_category"] == "sales"
    assert f["profile"]["row_count"] == 5


def test_data_quality_score_reflects_issues(client):
    f = _messy(client).json()["report"]["files"][0]
    dq = f["data_quality"]
    assert dq["score"] < 100.0
    assert dq["findings_count"] > 0
    by_name = {c["check"]: c for c in dq["checks"]}
    assert "duplicate_rows" in by_name
    assert "empty_or_null" in by_name
    assert "type_coercion" in by_name
    assert "negative_values" in by_name


def test_nulls_and_values_reported(client):
    f = _messy(client).json()["report"]["files"][0]
    by_name = {c["check"]: c for c in f["data_quality"]["checks"]}
    assert by_name["duplicate_rows"]["count"] >= 1
    assert by_name["negative_values"]["by_column"].get("total_amount", 0) >= 1
    assert by_name["type_coercion"]["by_column"].get("unit_price", 0) >= 1
    assert by_name["empty_or_null"]["empty_file"] is False


def test_sanitize_keeps_report_json_serializable(client):
    """An analysis stored and re-fetched must survive str() cast of NaN etc."""
    created = _messy(client).json()
    body = client.get(
        f"/api/v1/applications/{created['application_id']}/analyses/{created['analysis_id']}",
        headers=api_headers(),
    )
    assert body.status_code == 200
    assert "sales" in body.json()["summary"]["categories_detected"]