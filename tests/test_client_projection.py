"""The client-facing JSON contract and the Excel reader fallback.

Two things are pinned here:

* ``format=client`` must satisfy SiroQ-Client's ``ReportResultData``
  (``{ [key: string]: ReportNode }``) -- a recursive label->value tree whose
  leaves are primitives. The client renders it with ``Object.entries`` plus one
  recursive walk, so a list at the top level or an unexpected scalar type would
  degrade the report rather than fail loudly here.
* the raw ``format=json`` document must stay exactly as it was, because other
  consumers already depend on it.
"""
from io import BytesIO

import pytest

from app.analytics_service.ingestion import _read_excel_source, read_file
from app.analytics_service.reporting import (
    CLIENT_SCHEMA_VERSION,
    _agg_severity,
    build_client_projection,
    build_file_model,
)
from tests.conftest import api_headers

CLEAN_SALES_CSV = """sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,cash
2026-09-02,TX-002,Ibuprofen 400mg,1,20.00,20.00,cash
2026-09-03,TX-003,Paracetamol 500mg,3,12.50,37.50,insurance
2026-09-04,TX-004,Amoxicillin 250mg,1,15.00,15.00,insurance
"""


# Same shape as the clean file, but the transaction_ref column has nulls and a
# repeat, which is what produces quality findings to count.
MESSY_SALES_CSV = """sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method
2026-09-01,TX-001,Paracetamol 500mg,2,12.50,25.00,cash
2026-09-02,,Ibuprofen 400mg,1,20.00,20.00,cash
2026-09-03,TX-001,Paracetamol 500mg,3,12.50,37.50,insurance
2026-09-04,,Amoxicillin 250mg,1,15.00,15.00,insurance
"""


def _upload(client, name="projection-app", csv=CLEAN_SALES_CSV, filename="sales.csv"):
    r = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files=[("files", (filename, BytesIO(csv.encode()), "text/csv"))],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _upload_named(client, entries, name="multi-app"):
    """Upload several files at once, each with its own (filename, csv) pair."""
    r = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files=[
            ("files", (fn, BytesIO(csv.encode()), "text/csv")) for fn, csv in entries
        ],
    )
    assert r.status_code == 200, r.text
    return r.json()


def _client_report(client, app_id, analysis_id):
    r = client.get(
        f"/api/v1/applications/{app_id}/analyses/{analysis_id}/report?format=client",
        headers=api_headers(),
    )
    assert r.status_code == 200, r.text
    return r.json()


def _walk(node, path="root"):
    """Yield (path, value) for every leaf so shape rules can be asserted once."""
    if isinstance(node, dict):
        for key, val in node.items():
            yield from _walk(val, f"{path}.{key}")
    elif isinstance(node, list):
        assert all(isinstance(i, (str, int, float, bool)) for i in node), (
            f"{path} holds non-scalar list items: {node!r}"
        )
        for idx, val in enumerate(node):
            yield from _walk(val, f"{path}[{idx}]")
    else:
        yield path, node


class TestClientProjectionShape:
    def test_matches_report_result_data_contract(self, client):
        body = _upload(client)
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        app_id = apps[0]["id"]
        aid = body["analysis_id"]

        r = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=client",
            headers=api_headers(),
        )
        assert r.status_code == 200, r.text
        proj = r.json()

        # ReportResultData is an object of ReportNode, never a bare array
        assert isinstance(proj, dict)
        # every leaf is a primitive the client can format without surprises
        for path, value in _walk(proj):
            assert isinstance(value, (str, int, float, bool, type(None))), path

    def test_headline_metrics_are_flat_and_preformatted(self, client):
        body = _upload(client)
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        app_id = apps[0]["id"]
        aid = body["analysis_id"]
        proj = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=client",
            headers=api_headers(),
        ).json()

        assert proj["Schema version"] == CLIENT_SCHEMA_VERSION
        for key in (
            "Files analyzed",
            "Records examined",
            "Data quality score",
            "Quality verdict",
            "Findings requiring review",
        ):
            assert key in proj, f"missing headline metric {key!r}"
            # flat headline: a label the reviewer scans, mapped to a scalar
            assert isinstance(proj[key], (str, int, float)), key

        # a label list is a valid ReportNode (array of scalars), but must not be
        # a bare array at the top level
        assert "Categories detected" in proj
        assert isinstance(proj["Categories detected"], list)
        assert all(isinstance(x, str) for x in proj["Categories detected"])

        assert proj["Files analyzed"] == "1"
        assert proj["Records examined"] == "4"
        assert proj["Quality verdict"] in {
            "Strong", "Acceptable", "Review before accepting",
            "Action required", "Not scored",
        }

    def test_per_file_drilldown_is_nested_under_filename(self, client):
        body = _upload(client)
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        app_id = apps[0]["id"]
        aid = body["analysis_id"]
        proj = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=client",
            headers=api_headers(),
        ).json()

        files = proj["Files"]
        assert "sales.csv" in files
        entry = files["sales.csv"]
        assert entry["Rows"] == "4"
        assert "Quality" in entry
        assert "Score" in entry["Quality"]
        # domain metrics are surfaced under a reviewable heading
        assert any(k in entry for k in ("Business metrics", "Evidence gaps",
                                        "Insights", "Column profile"))

    def test_multiple_files_each_get_a_branch(self, client):
        r = client.post(
            "/api/v1/analyze",
            headers=api_headers(),
            data={"application_name": "multi-projection"},
            files=[
                ("files", ("a.csv", BytesIO(CLEAN_SALES_CSV.encode()), "text/csv")),
                ("files", ("b.csv", BytesIO(CLEAN_SALES_CSV.encode()), "text/csv")),
            ],
        )
        assert r.status_code == 200, r.text
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        app_id = apps[0]["id"]
        aid = r.json()["analysis_id"]
        proj = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=client",
            headers=api_headers(),
        ).json()
        assert proj["Files analyzed"] == "2"
        assert set(proj["Files"]) == {"a.csv", "b.csv"}

    def test_same_filename_twice_keeps_both_files(self, client):
        """Two uploads can share a filename. Keying on the bare name dropped one.

        Nothing prevented two files called `stock.csv` in one application — two
        vendors both send it. The client renders `Files` as an object, so the
        duplicate had to be numbered rather than silently overwriting the first
        branch, which made the per-file totals stop adding up to the headline.
        """
        body = _upload_named(
            client,
            [
                ("stock.csv", CLEAN_SALES_CSV),
                ("stock.csv", CLEAN_SALES_CSV.replace("TX-001", "TX-901")),
            ],
            name="duplicate-filenames",
        )
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        proj = _client_report(client, apps[0]["id"], body["analysis_id"])

        assert proj["Files analyzed"] == "2", "one of the two files vanished"
        keys = list(proj["Files"])
        assert len(keys) == 2, f"branches collapsed onto {keys!r}"
        assert len(set(keys)) == 2, f"duplicate branch keys: {keys!r}"
        # first occurrence keeps the plain name; the repeat is disambiguated
        assert "stock.csv" in keys
        assert any(k.startswith("stock.csv") and k != "stock.csv" for k in keys), keys
        # and both branches are real, distinct reports
        rows = [proj["Files"][k]["Rows"] for k in keys]
        assert rows == ["4", "4"], rows

    def test_highest_signal_reflects_what_the_engine_flagged(self):
        """The headline severity has to come from the real insights.

        The aggregator was handed file models — whose `insights` are already
        grouped — and re-ran the grouper on that dict. It returned nothing, so
        every application reported "Highest signal: Info" no matter what the
        engine had flagged, which is the one field a reviewer is most likely to
        act on.
        """
        def signal(*severities):
            insights = [
                {
                    "family": "sales",
                    "key": f"k{i}",
                    "label": f"Insight {i}",
                    "value": 1.0,
                    "status": "ok",
                    "severity": sev,
                }
                for i, sev in enumerate(severities)
            ]
            return _agg_severity([build_file_model({"filename": "x.csv", "insights": insights})])

        assert signal("critical") == "critical"
        assert signal("info", "warning") == "warning"
        assert signal("info", "critical", "warning") == "critical"
        assert signal("info") == "info"
        # a file with no computable insights is not itself a signal
        assert signal() == "info"

    def test_handles_empty_and_absent_report_without_raising(self):
        assert build_client_projection(None, None)["Schema version"] == CLIENT_SCHEMA_VERSION
        empty = build_client_projection({}, {})
        assert empty["Files analyzed"] == "0"
        assert empty["Data quality score"] == "Not scored"

    def test_read_errors_surface_rather_than_vanish(self):
        """A file the engine could not read must be visible, not silently absent."""
        proj = build_client_projection(
            {
                "application_name": "broken",
                "files": [{"filename": "bad.xlsx", "errors": ["unreadable"]}],
            },
            {"file_count": 1},
        )
        assert proj["Files"]["bad.xlsx"]["Read errors"] == ["unreadable"]

    def test_per_file_findings_count_is_a_number_not_none(self, client):
        """Per-file Findings has to agree with the headline.

        This printed the literal string `None` for every file because the code
        read a `findings_count` key the file model never sets, so a reviewer
        drilling into the one file that actually had findings was told there
        were none — while the headline said otherwise.
        """
        body = _upload_named(
            client,
            [("messy.csv", MESSY_SALES_CSV), ("clean.csv", CLEAN_SALES_CSV)],
            name="findings-count",
        )
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        proj = _client_report(client, apps[0]["id"], body["analysis_id"])

        for name, entry in proj["Files"].items():
            assert entry["Quality"]["Findings"] != "None", (
                f"{name} reported Findings as the string None"
            )
            # a count is always a plain integer string
            assert entry["Quality"]["Findings"].isdigit(), (name, entry["Quality"]["Findings"])
            detail = entry["Quality"].get("Finding detail") or []
            assert entry["Quality"]["Findings"] == str(len(detail)), name

        # the per-file counts must sum to the headline, so the two views agree
        total = sum(int(e["Quality"]["Findings"]) for e in proj["Files"].values())
        assert str(total) == proj["Findings requiring review"], (
            f"per-file total {total} != headline {proj['Findings requiring review']}"
        )


class TestDownloadContract:
    def _ids(self, client):
        body = _upload(client, name="download-contract")
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        return apps[0]["id"], body["analysis_id"]

    def test_filename_stem_is_safe_for_content_disposition(self, client):
        """Application names are free text and land in a quoted filename.

        A name containing a quote or a newline corrupts the header, and a name
        that is entirely non-ASCII used to leave an empty stem.
        """
        from app.routers.analyses import _ascii_filename

        assert _ascii_filename('Acme "Pharma" Ltd') == "Acme-Pharma-Ltd"
        assert _ascii_filename("bad\nname") == "bad-name"
        assert _ascii_filename("مسودة/تقرير") == "app"  # no ASCII at all
        assert _ascii_filename("   ") == "app"
        assert _ascii_filename("...") == "app"  # not a parent-directory reference
        assert _ascii_filename("New Report") == "New-Report"

    def test_client_download_is_attachment_with_stable_filename(self, client):
        app_id, aid = self._ids(client)
        r = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=client",
            headers=api_headers(),
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("application/json")
        disp = r.headers["content-disposition"]
        assert disp.startswith("attachment;")
        # filename comes from the server, not guessed by a browser
        assert "download-contract" in disp, disp
        assert "siroq-client-" in disp, disp
        assert f"{aid[:8]}.json" in disp, disp

    def test_raw_json_download_is_unchanged(self, client):
        """format=json is a pre-existing contract; this task must not alter it."""
        app_id, aid = self._ids(client)
        r = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=json",
            headers=api_headers(),
        )
        assert r.status_code == 200, r.text
        doc = r.json()
        assert "files" in doc, "raw report document shape changed"
        assert doc["files"][0]["profile"], "raw profile detail was dropped"
        assert "siroq-report-" in r.headers["content-disposition"]
        assert f"{aid[:8]}.json" in r.headers["content-disposition"]

    def test_printable_html_offers_both_downloads(self, client):
        app_id, aid = self._ids(client)
        r = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=html",
            headers=api_headers(),
        )
        assert r.status_code == 200, r.text
        body = r.text
        assert "format=client" in body, "printable page must offer the client JSON"
        assert "format=json" in body, "printable page must offer the raw JSON"

    def test_printable_downloads_are_keyed_not_plain_links(self, client):
        """A bare <a href> to an API route would 401 on click.

        The export routes are API-key guarded and a browser navigation cannot
        carry the X-API-Key header, so the printable page must fetch with the
        header. Guard the regression explicitly.
        """
        app_id, aid = self._ids(client)
        body = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=html",
            headers=api_headers(),
        ).text
        assert "<a href=\"{{ client_url }}\"" not in body, (
            "printable downloads must not be plain links"
        )
        assert "data-dl=" in body, "download buttons must carry their endpoint"
        assert "X-API-Key" in body, "printable downloads must send the API key"
        assert "siroq_api_key" in body, "printable downloads must read the stored key"

    def test_unknown_format_is_rejected(self, client):
        app_id, aid = self._ids(client)
        r = client.get(
            f"/api/v1/applications/{app_id}/analyses/{aid}/report?format=pdf",
            headers=api_headers(),
        )
        assert r.status_code == 422, r.text


class TestExcelReaderFallback:
    def test_calamine_is_preferred(self, monkeypatch):
        """calamine must stay first: it is the only engine that tolerates the
        invalid Crystal Reports stylesheets openpyxl rejects."""
        import pandas as pd

        import app.analytics_service.ingestion as ing

        tried: list[str] = []

        real = pd.read_excel

        def spy(content, **kw):
            tried.append(kw.get("engine"))
            return real(content, **kw)

        monkeypatch.setattr(ing.pd, "read_excel", spy)
        try:
            _read_excel_source(_xlsx_bytes())
        except Exception:
            pass
        assert tried[0] == "calamine"

    def test_falls_back_to_openpyxl_when_calamine_missing(self, monkeypatch):
        import pandas as pd

        import app.analytics_service.ingestion as ing

        real = pd.read_excel

        def calamine_missing(content, **kw):
            if kw.get("engine") == "calamine":
                raise ImportError("Import python-calamine failed")
            return real(content, **kw)

        monkeypatch.setattr(ing.pd, "read_excel", calamine_missing)
        buf = _xlsx_bytes()
        result = _read_excel_source(buf)
        assert result.sheets, "fallback engine produced no sheets"

    def test_both_engines_failing_is_recorded_as_an_error(self, monkeypatch):
        """Never a silent empty analysis: a workbook we cannot read must say so."""
        import pandas as pd

        import app.analytics_service.ingestion as ing

        def all_missing(content, **kw):
            raise ImportError(f"Import {kw.get('engine')} failed")

        monkeypatch.setattr(ing.pd, "read_excel", all_missing)
        ingested = read_file(_xlsx_bytes(), "workbook.xlsx")
        assert ingested.errors, "unreadable workbook produced no error"
        assert "no Excel engine" in " ".join(ingested.errors)
        assert ingested.row_count == 0


def _xlsx_bytes():
    """A minimal real .xlsx, written through pandas so it is not a fixture blob."""
    import pandas as pd

    buf = BytesIO()
    with pd.ExcelWriter(buf) as writer:
        pd.DataFrame({"sale_id": [1, 2], "total_amount": [10.0, 20.0]}).to_excel(
            writer, sheet_name="Sales", index=False
        )
        pd.DataFrame({"rx_number": [1], "ndc": [123]}).to_excel(
            writer, sheet_name="Prescriptions", index=False
        )
    return buf.getvalue()
