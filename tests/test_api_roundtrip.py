"""End-to-end: one-shot upload -> deep analysis persisted -> retrieval."""
from io import BytesIO

from tests.conftest import api_headers

CLEAN_SALES_CSV = """sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,cash
2026-09-01,TX-002,Ibuprofen 400mg,1,20.00,20.00,cash
2026-09-02,TX-003,Paracetamol 500mg,3,12.50,37.50,insurance
2026-09-02,TX-004,Amoxicillin 250mg,1,15.00,15.00,insurance
2026-09-03,TX-005,Paracetamol 500mg,1,12.50,12.50,cash
"""


def _upload(client, name="pharmacy-a", csv=CLEAN_SALES_CSV, filename="sales.csv"):
    return client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files=[("files", (filename, BytesIO(csv.encode()), "text/csv"))],
    )


def test_one_shot_upload_accepts_multiple_files(client):
    r = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": "multi"},
        files=[
            ("files", ("a.csv", BytesIO(CLEAN_SALES_CSV.encode()), "text/csv")),
            ("files", ("b.csv", BytesIO(CLEAN_SALES_CSV.encode()), "text/csv")),
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["file_count"] == 2
    apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
    detail = client.get(
        f"/api/v1/applications/{apps[0]['id']}", headers=api_headers()
    ).json()
    assert len(detail["files"]) == 2
    assert {f["original_filename"] for f in detail["files"]} == {"a.csv", "b.csv"}


def test_one_shot_upload_analyzes_and_persists(client):
    r = _upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["application_id"]
    assert body["analysis_id"]
    assert body["summary"]["file_count"] == 1
    assert body["summary"]["total_rows"] == 5
    assert body["summary"]["categories_detected"]["sales"] == ["sales.csv"]

    report = body["report"]
    assert report["application_name"] == "pharmacy-a"
    f = report["files"][0]
    assert f["top_category"] == "sales"
    assert f["domain_analytics"]["revenue"] == 110.0
    assert f["data_quality"]["score"] == 100.0
    assert f["profile"]["column_count"] == 7
    assert f["classification"]["field_scores"]["total_amount"]["status"] == "confirmed"
    assert f["classification"]["field_scores"]["sale_timestamp"]["status"] == "confirmed"


def test_full_report_retrievable_by_analysis_id(client):
    created = _upload(client).json()
    r = client.get(
        f"/api/v1/applications/{created['application_id']}/analyses/{created['analysis_id']}",
        headers=api_headers(),
    )
    assert r.status_code == 200
    got = r.json()
    assert got["application_id"] == created["application_id"]
    assert got["analysis_id"] == created["analysis_id"]
    assert got["report"] == created["report"]
    assert got["summary"] == created["summary"]


def test_application_detail_lists_files_and_analyses(client):
    created = _upload(client).json()
    r = client.get(
        f"/api/v1/applications/{created['application_id']}", headers=api_headers()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "pharmacy-a"
    assert len(body["files"]) == 1
    assert body["files"][0]["original_filename"] == "sales.csv"
    assert body["files"][0]["sha256"]
    assert [a["id"] for a in body["analyses"]] == [created["analysis_id"]]


def test_raw_file_download(client):
    created = _upload(client).json()
    file_id = created["report"]["files"][0]["file_id"]
    r = client.get(f"/api/v1/files/{file_id}", headers=api_headers())
    assert r.status_code == 200
    assert r.content.decode() == CLEAN_SALES_CSV
    assert r.headers["X-SHA256"] == created["report"]["files"][0]["sha256"]


def test_reanalyze_creates_new_analysis(client):
    created = _upload(client).json()
    r = client.post(
        f"/api/v1/applications/{created['application_id']}/analyze",
        headers=api_headers(),
    )
    assert r.status_code == 200
    newer = r.json()
    assert newer["analysis_id"] != created["analysis_id"]
    got = client.get(
        f"/api/v1/applications/{created['application_id']}",
        headers=api_headers(),
    ).json()
    assert len(got["analyses"]) == 2


def test_upload_to_existing_application(client):
    created = _upload(client, name="multi-feed").json()
    app_id = created["application_id"]
    second = _upload(client, name="multi-feed", csv=CLEAN_SALES_CSV, filename="extra.csv").json()
    assert second["application_id"] == app_id
    assert second["summary"]["file_count"] == 2


def test_application_not_found(client):
    r = client.get("/api/v1/applications/does-not-exist", headers=api_headers())
    assert r.status_code == 404


def test_create_application_is_idempotent_by_name(client):
    first = client.post(
        "/api/v1/applications",
        headers=api_headers(),
        json={"name": "dup-name", "metadata": {"a": 1}},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/applications",
        headers=api_headers(),
        json={"name": "dup-name", "metadata": {"b": 2}},
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["metadata"] == {"b": 2}


def test_two_uploads_to_same_new_name_share_one_application(client):
    one = _upload(client, name="race", filename="a.csv").json()
    two = _upload(client, name="race", filename="b.csv").json()
    assert one["application_id"] == two["application_id"]
    assert two["summary"]["file_count"] == 2


def test_multi_sheet_excel_analyzes_each_sheet(client):
    import io

    import pandas as pd

    buf = io.BytesIO()
    sales = pd.DataFrame(
        {"sale_id": [1], "receipt_id": [1], "total_amount": [10.0], "payment_method": ["cash"]}
    )
    rx = pd.DataFrame(
        {"rx_number": [1], "days_supply": [30], "prescriber_id": [9], "ndc": [123]}
    )
    with pd.ExcelWriter(buf) as writer:
        sales.to_excel(writer, sheet_name="Sales", index=False)
        rx.to_excel(writer, sheet_name="Prescriptions", index=False)
    buf.seek(0)

    r = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": "multi-sheet-app"},
        files=[("files", ("workbook.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )
    assert r.status_code == 200, r.text
    f = r.json()["report"]["files"][0]
    assert f["multi_sheet"] is True
    assert f["sheet_count"] == 2
    assert f["row_count"] == 2
    assert f["top_category"] is None
    sheet_cats = {entry["sheet"]: entry["category"] for entry in f["sheet_categories"]}
    assert sheet_cats["Sales"] == "sales"
    assert sheet_cats["Prescriptions"] == "prescriptions"
    summary = r.json()["summary"]
    assert summary["categories_detected"]["sales"] == ["workbook.xlsx[Sales]"]
    assert summary["categories_detected"]["prescriptions"] == ["workbook.xlsx[Prescriptions]"]
    # ndc is a drug identifier, not a business field: it must stay unmapped
    # instead of being inferred as total_amount, so it costs the 5-point penalty
    # and raises one unmapped-column finding.
    assert summary["data_quality_score"] == 97.5
    assert summary["findings_count"] == 1