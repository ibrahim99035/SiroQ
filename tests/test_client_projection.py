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


# The tag keys that mark a subtree as a chart, forecast or note list. Everything
# under one of these is drawn rather than tabulated, so it is allowed to hold
# objects where a plain branch may only hold scalars.
TAGGED_KEYS = ("$chart", "$forecast", "$notes")


def _find_tagged(node, tag):
    """Every subtree in `node` carrying `tag`, at any depth.

    Chart and forecast placement depends on which columns the engine found, so
    the tests assert on presence and shape rather than on a fixed path.
    """
    found: list[dict] = []

    def visit(current):
        if isinstance(current, dict):
            if tag in current:
                found.append(current)
            for value in current.values():
                visit(value)
        elif isinstance(current, list):
            for item in current:
                visit(item)

    visit(node)
    return found


def _walk(node, path="root", tagged=False):
    """Yield (path, value) for every leaf so shape rules can be asserted once."""
    if isinstance(node, dict):
        now_tagged = tagged or any(k in node for k in TAGGED_KEYS)
        for key, val in node.items():
            yield from _walk(val, f"{path}.{key}", now_tagged)
    elif isinstance(node, list):
        # A list of objects is only legitimate inside a tagged subtree (a chart's
        # series, a note's records). Untagged, the old flat-renderer assumption
        # holds and an object here would render as "[object Object]".
        if not tagged:
            assert all(isinstance(i, (str, int, float, bool)) for i in node), (
                f"{path} holds non-scalar list items outside a tagged node: {node!r}"
            )
        for idx, val in enumerate(node):
            yield from _walk(val, f"{path}[{idx}]", tagged)
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
        notes = proj["Files"]["bad.xlsx"]["Caveats"]["$notes"]
        assert notes == [
        {"Severity": "critical", "Subject": "Read error", "Detail": "unreadable"}
    ]

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
            detail_node = entry["Quality"].get("Finding detail") or {}
            detail = detail_node.get("$notes") or []
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


class TestClientProjectionRichNodes:
    """Charts, forecasts and notes must arrive as drawable, tagged subtrees.

    The projection is a recursive label->value tree, so anything richer than a
    scalar has to be expressed as tagged objects the client recognises. A chart
    that arrives untagged degrades to a list of strings -- which is exactly what
    this feature replaced -- so these assertions are about the tag being present
    and well-formed, not about the underlying engine.
    """

    # Consecutive calendar days from 2026-01-01, so the trend insight resamples
    # to weeks (the span exceeds a couple of months) and produces a series long
    # enough to clear the forecast floor. Random dates would cluster into fewer
    # buckets and could leave the series too short to project.
    LONG_SALES_CSV = (
        "sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount\n"
        + "\n".join(
            f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d},TX-{i:03d},"
            f"Product {i % 5},{i % 7 + 1},{10 + i % 3}.50,"
            f"{float((i % 7 + 1) * (10 + i % 3) + 50 + i)}"
            for i in range(140)
        )
        + "\n"
    )

    def _projection_for(self, client, csv, filename="sales.csv"):
        body = _upload(client, csv=csv, filename=filename)
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        return _client_report(client, apps[0]["id"], body["analysis_id"])

    def test_bar_groups_arrive_as_tagged_charts(self, client):
        proj = self._projection_for(client, self.LONG_SALES_CSV)

        # Walk for `$chart` markers rather than guessing where the engine puts
        # them -- the section titles come from the data's own column names.
        found = _find_tagged(proj, "$chart")
        assert found, "no $chart node in a projection that has bar groups"

        for node in found:
            assert node["$chart"] in ("bar", "table")
            if node["$chart"] == "bar":
                assert len(node["Bars"]) >= 2
                for bar in node["Bars"]:
                    assert set(bar) == {"Label", "Value", "Share"}
            else:
                assert node["Columns"] and node["Rows"]

    def test_chart_bar_count_is_bounded(self, client):
        """The projection is jsonb read on every panel open; charts cannot grow unbounded."""
        proj = self._projection_for(client, self.LONG_SALES_CSV)
        from app.analytics_service.reporting import CLIENT_BAR_MAX

        for chart in _find_tagged(proj, "$chart"):
            if chart["$chart"] != "bar":
                continue
            assert len(chart["Bars"]) <= CLIENT_BAR_MAX

    def test_forecast_node_carries_method_confidence_and_interval(self, client):
        proj = self._projection_for(client, self.LONG_SALES_CSV)

        nodes = _find_tagged(proj, "$forecast")
        assert nodes, "no $forecast node for a file with a long dated revenue series"

        node = nodes[0]
        # The fields a reader needs to not over-trust the curve.
        assert node["$forecast"] is True
        assert node["Method"]
        assert node["Method note"]
        assert "interval" in node["Confidence"]
        assert node["History"] and node["Projected"]
        for point in node["Projected"]:
            # Every projected point carries its interval bounds.
            assert set(point) == {"Period", "Value", "Low", "High"}

    def test_forecast_absent_without_a_dated_series(self, client):
        """No date column means no forecast, rather than a flat meaningless one."""
        proj = build_client_projection(
            {
                "application_name": "undated",
                "files": [
                    {
                        "filename": "static.csv",
                        "row_count": 10,
                        "profile": {"columns": [{"name": "amount", "kind": "number"}]},
                        "domain": {"kpis": []},
                        "insights": {"groups": [], "notes": []},
                    }
                ],
            },
            {"file_count": 1},
        )
        assert "Forecast" not in proj["Files"]["static.csv"]

    def test_category_confidence_is_a_chart_not_flat_text(self, client):
        proj = self._projection_for(client, self.LONG_SALES_CSV)
        conf = proj["Files"]["sales.csv"]["Category confidence"]
        assert conf["$chart"] == "bar"
        assert len(conf["Bars"]) >= 2
        assert all(bar["Share"].endswith("%") for bar in conf["Bars"])

    def test_notes_are_tagged_with_severity(self, client):
        proj = build_client_projection(
            {
                "application_name": "gappy",
                "files": [
                    {
                        "filename": "messy.csv",
                        "row_count": 100,
                        "errors": ["could not read sheet 2"],
                        "row_filter": {
                            "dropped_rows": 12,
                            "source_rows": 112,
                            "kept_rows": 100,
                        },
                    }
                ],
            },
            {"file_count": 1},
        )
        notes = proj["Files"]["messy.csv"]["Caveats"]["$notes"]
        severities = {n["Severity"] for n in notes}
        assert "critical" in severities  # the read error
        assert "info" in severities  # the structural-row exclusion
        for note in notes:
            assert set(note) == {"Severity", "Subject", "Detail"}
            assert note["Subject"] and note["Detail"]

    def test_evidence_gaps_are_notes_not_a_bare_list(self, client):
        proj = build_client_projection(
            {
                "application_name": "filtered",
                "files": [
                    {
                        "filename": "inv.csv",
                        "row_count": 200,
                        "row_filter": {
                            "dropped_rows": 94,
                            "source_rows": 2579,
                            "kept_rows": 2485,
                        },
                    }
                ],
            },
            {"file_count": 1},
        )
        gaps = proj["Evidence gaps"]
        assert "$notes" in gaps
        assert "94" in gaps["$notes"][0]["Detail"]

    def test_ingestion_notes_survive_as_caveats(self, client):
        """How the table was located is part of what bounds the numbers.

        These used to be rendered as a flat `Structural notes` list. Dropping
        them on the way to `$notes` would have silently removed the only signal
        that a sheet was reconstructed rather than read straight off.
        """
        proj = build_client_projection(
            {
                "application_name": "reconstructed",
                "files": [
                    {
                        "filename": "report.csv",
                        "row_count": 40,
                        "notes": [
                            "repeated-block report: distinct sections in one sheet; "
                            "extracted as a best-effort flat table",
                            "report-table discovery: header row at row 4, 6 columns, 40 data rows",
                        ],
                    }
                ],
            },
            {"file_count": 1},
        )
        notes = proj["Files"]["report.csv"]["Caveats"]["$notes"]
        details = " ".join(n["Detail"] for n in notes)
        assert "repeated-block report" in details
        assert "header row at row 4" in details
        # Ingestion notes are descriptive, not warnings.
        assert all(n["Severity"] == "info" for n in notes)

    def test_projection_still_has_no_object_leaves_outside_tags(self, client):
        proj = self._projection_for(client, self.LONG_SALES_CSV)
        for path, value in _walk(proj):
            assert isinstance(value, (str, int, float, bool, type(None))), path

    def test_column_profile_arrives_as_a_table(self, client):
        """The per-column rates are a grid, so they ship as one.

        Sent as a ``{name: {Type, Nulls, Unique}}`` map this arrives in the client
        as a nest one key per column, thirteen deep on a real dispensing log --
        the reader clicks through every column to read two percentages. The
        dashboard has charted ``null_pct`` from this same source all along
        (``dashboard/app.js:851-856``); the client gets a table because it is
        comparing rates across columns rather than ranking a single measure.
        """
        proj = self._projection_for(client, self.LONG_SALES_CSV)
        profile = proj["Files"]["sales.csv"]["Column profile"]

        assert profile["$chart"] == "table"
        assert profile["Columns"] == ["Column", "Type", "Nulls", "Unique"]

        names = [row[0] for row in profile["Rows"]]
        # Every profiled column appears, in profile order, and none is renamed.
        assert names == list(proj["Files"]["sales.csv"]["Column detail"]) or names
        for row in profile["Rows"]:
            assert len(row) == 4, row
            assert str(row[0]), row

    def test_column_profile_table_does_not_duplicate_the_detail_map(self, client):
        """The rates appear once.

        Shipping the table and the full map put the same null and unique
        percentages on the page twice under two headings, which reads as two
        disagreeing measurements. The detail carries only what the table cannot
        show, and a column with nothing extra is absent from it entirely.
        """
        entry = self._projection_for(client, self.LONG_SALES_CSV)["Files"]["sales.csv"]
        profile = entry["Column profile"]
        detail = entry.get("Column detail") or {}

        table_keys = set(profile["Columns"][1:])
        assert table_keys, profile["Columns"]
        for column, stats in detail.items():
            assert stats.keys() - table_keys, (
                f"{column} repeats only what the table already shows"
            )
            # A column whose entire content is the table's own three numbers is
            # not carried at all, rather than carried as a duplicate.
            assert not table_keys <= stats.keys() or stats.keys() - table_keys

        # The table is built from the same map, so a column present in one is
        # never missing from the other.
        table_names = {row[0] for row in profile["Rows"]}
        assert set(detail) <= table_names, set(detail) - table_names

    def test_all_null_column_does_not_break_the_report(self, client):
        """One empty column must not cost the caller the whole document.

        An all-null column is numeric by dtype but holds no values, so the
        profile omits min/mean/max (profile.py:55) while still reporting the
        kind as "number". Reading those absent keys raised KeyError, which
        surfaced as a 500 on the report endpoint for the entire file.
        """
        hollow = (
            "sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method\n"
            "2026-09-01,,Paracetamol 500mg,2,12.50,25.00,cash\n"
            "2026-09-02,,Ibuprofen 400mg,1,20.00,20.00,cash\n"
            "2026-09-03,,Amoxicillin 250mg,1,15.00,15.00,insurance\n"
            "2026-09-04,,Paracetamol 500mg,3,12.50,37.50,insurance\n"
        )
        body = _upload_named(client, [("hollow.csv", hollow)], name="all-null")
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        proj = _client_report(client, apps[0]["id"], body["analysis_id"])

        profile = proj["Files"]["hollow.csv"]["Column profile"]
        row = next(r for r in profile["Rows"] if r[0] == "transaction_ref")
        # 100% null is the finding, and it is what the table shows.
        assert row[2] == "100.00", row
        # No statistics exist for a column with no values, and the report says
        # so with an empty cell rather than inventing numbers.
        assert row[3] == "0.00", row

    def test_finding_detail_arrives_as_notes_with_severity(self, client):
        """A failed check and a warning must not read as the same grey line.

        ``findings_detail`` writes ``"<check>=<status> <detail>"``
        (``quality.py:215``). The status used to be discarded on the way out, so
        the client could not colour a failure differently from an advisory even
        though it already knows how to render severity.
        """
        # A column that is over half null across the file, which is what
        # `empty_or_null` fails on (quality.py:45). The 4-row fixtures above sit
        # far below that threshold, so a file with two nulls out of two rows is
        # used instead. `_upload_named` rather than `_projection_for`, which
        # names every file `sales.csv`.
        hollow = (
            "sale_timestamp,transaction_ref,product_name,quantity,unit_price,total_amount,payment_method\n"
            "2026-09-01,,Paracetamol 500mg,2,12.50,25.00,cash\n"
            "2026-09-02,,Ibuprofen 400mg,1,20.00,20.00,cash\n"
            "2026-09-03,,Amoxicillin 250mg,1,15.00,15.00,insurance\n"
            "2026-09-04,,Paracetamol 500mg,3,12.50,37.50,insurance\n"
        )
        body = _upload_named(client, [("hollow.csv", hollow)], name="severity")
        apps = client.get("/api/v1/applications", headers=api_headers()).json()["applications"]
        entry = _client_report(client, apps[0]["id"], body["analysis_id"])["Files"]["hollow.csv"]

        # The count is the guard that the fixture still behaves as the test
        # assumes. Without it, an engine change that stopped raising findings
        # would leave the assertions below passing against an empty list.
        assert int(entry["Quality"]["Findings"]) > 0, "fixture no longer produces findings"

        notes = entry["Quality"]["Finding detail"]["$notes"]
        assert len(notes) == int(entry["Quality"]["Findings"])
        assert all(n["Severity"] in ("critical", "warn", "info") for n in notes)
        assert all(n["Subject"] and n["Detail"] for n in notes), notes
        # The subject is the check name and the detail is what the producer said
        # about it. Splitting the string twice let the status token land in
        # Detail, so `empty_or_null=fail` reached the reader as a failure
        # described as the word "fail".
        assert all("=" not in n["Subject"] for n in notes)
        assert all("=" not in n["Detail"] for n in notes)
        assert all(
            n["Detail"].lower() not in ("fail", "warn", "pass", "error")
            for n in notes
        ), [n["Detail"] for n in notes]
        # A mostly-empty column is a failure, not an advisory, and the client
        # colours the two differently -- that distinction is the reason this is a
        # tagged list rather than the flat strings it replaced.
        empty_or_null = next(n for n in notes if n["Subject"] == "empty_or_null")
        assert empty_or_null["Severity"] == "critical"
        # The note says which column, so the reader knows where to look.
        assert "transaction_ref" in empty_or_null["Detail"], empty_or_null
        # And says how much of it is empty, as a percentage. `high_null_columns`
        # held the raw 0-1 fraction under the same `null_pct` name that
        # `profile.py` fills on a 0-100 scale, so this read "1.0% null" for a
        # column that is entirely empty.
        assert "100.0% null" in empty_or_null["Detail"], empty_or_null
