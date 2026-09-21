"""Dashboard + report engine endpoints.

The dashboard reads the same /api/v1 JSON the CLI uses and adds two things:
a printable HTML report per saved analysis (rendered from the persisted report
document) and a static single-page frontend served under /dashboard.
"""
from tests.conftest import api_headers
from tests.test_report_export_ingestion import _crystal_report_xlsx


def _analyze(client, name="دار صيدلية"):
    content = _crystal_report_xlsx()
    res = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files={"files": ("sales.xlsx", content, "application/vnd.ms-excel")},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_analyze_populates_applications_list(client):
    resp = _analyze(client)
    aid, anid = resp["application_id"], resp["analysis_id"]
    body = client.get("/api/v1/applications", headers=api_headers()).json()
    apps = body["applications"]
    assert len(apps) == 1
    app = apps[0]
    assert app["id"] == aid
    assert app["name"] == "دار صيدلية"
    assert app["file_count"] == 1
    assert app["analysis_count"] == 1
    assert app["latest_analysis"]["id"] == anid
    assert app["latest_analysis"]["status"] == "completed"
    assert resp["summary"] is not None
    assert resp["report"] is not None


def test_report_html_serves_printable_document(client):
    resp = _analyze(client)
    aid, anid = resp["application_id"], resp["analysis_id"]
    body = client.get(
        f"/api/v1/applications/{aid}/analyses/{anid}/report?format=html",
        headers=api_headers(),
    )
    assert body.status_code == 200
    assert body.headers["content-type"].startswith("text/html")
    html = body.text
    assert "<html" in html.lower()
    assert "دار صيدلية" in html
    assert anid[:8] in html
    assert "sales" in html  # category surfaced in the report
    assert "Print" in html or "Save PDF" in html


def test_report_json_matches_stored_report_document(client):
    resp = _analyze(client)
    aid, anid = resp["application_id"], resp["analysis_id"]
    body = client.get(
        f"/api/v1/applications/{aid}/analyses/{anid}/report?format=json",
        headers=api_headers(),
    )
    assert body.status_code == 200
    assert body.headers["content-type"].startswith("application/json")
    assert "attachment" in body.headers["content-disposition"]
    assert body.json() == resp["report"]


def test_report_rejects_unknown_format(client):
    resp = _analyze(client)
    aid, anid = resp["application_id"], resp["analysis_id"]
    body = client.get(
        f"/api/v1/applications/{aid}/analyses/{anid}/report?format=pdf",
        headers=api_headers(),
    )
    assert body.status_code == 422


def test_report_404_when_analysis_belongs_to_other_application(client):
    first = _analyze(client, "App A")
    second = _analyze(client, "App B")
    body = client.get(
        f"/api/v1/applications/{second['application_id']}/analyses/{first['analysis_id']}/report",
        headers=api_headers(),
    )
    assert body.status_code == 404


def test_api_requires_key_on_dashboard_endpoints(client):
    resp = _analyze(client)
    aid, anid = resp["application_id"], resp["analysis_id"]
    assert client.get("/api/v1/applications").status_code == 401
    assert (
        client.get(f"/api/v1/applications/{aid}/analyses/{anid}/report").status_code
        == 401
    )


def test_dashboard_static_frontend_served(client):
    home = client.get("/", follow_redirects=False)
    assert home.status_code in (301, 302, 307, 308)
    assert home.headers["location"].rstrip("/").endswith("/dashboard")
    page = client.get("/dashboard/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "SiroQ" in page.text
    assert 'id="appNav"' in page.text
    assert 'id="analysisNav"' in page.text
    assert 'id="categoryBox"' in page.text
    for asset in ("/dashboard/app.js", "/dashboard/charts.js", "/dashboard/styles.css"):
        assert client.get(asset).status_code == 200