"""Forecast engine + data tools endpoints (preview / series / forecast).

The engine must stay pure-statistics and fully deterministic: same input
always yields the identical payload. Methods covered: linear trend selection,
weekly seasonality, flat fallback to naive, tiny-history fallback, prediction
intervals clamped to non-negative for non-negative series.
"""
import io

import pytest

from app.analytics_service import forecast as fc
from tests.conftest import api_headers

TREND = [float(10 + 2 * i) for i in range(60)]
FLAT = [7.0] * 12


def _weekly(n=70, base=100.0, trend=0.2):
    return [base + trend * i + (30 if i % 7 < 5 else -10) for i in range(n)]


class TestEngine:
    def test_deterministic(self):
        a = fc.forecast(TREND, horizon=10)
        b = fc.forecast(TREND, horizon=10)
        assert a == b

    def test_trend_picks_linear(self):
        r = fc.forecast(TREND, horizon=10)
        assert r["method"] == "linear"
        assert len(r["forecast"]) == 10
        # slope should approximate the +2-per-step trend
        slope = (r["diagnostics"]["slope"] or 0)
        assert 1.4 < slope < 2.6

    def test_seasonal_captures_weekday_effect(self):
        r = fc.forecast(_weekly(), horizon=14)
        assert r["method"] == "seasonal"
        # weekday maxima should keep exceeding the weekend minima over the lead
        wd = [p["value"] for i, p in enumerate(r["forecast"]) if i % 7 < 5]
        we = [p["value"] for i, p in enumerate(r["forecast"]) if i % 7 >= 5]
        assert min(wd) > max(we)

    def test_flat_falls_back_to_naive(self):
        r = fc.forecast(FLAT, horizon=5)
        assert r["method"] == "naive"
        assert all(p["value"] == 7.0 for p in r["forecast"])

    def test_tiny_history_does_not_crash(self):
        r = fc.forecast([5.0], horizon=3)
        assert r["method"] == "naive"
        assert len(r["forecast"]) == 3

    def test_two_point_series(self):
        r = fc.forecast([10.0, 20.0], horizon=2)
        assert len(r["forecast"]) == 2
        assert r["forecast"][0]["value"] > 0

    def test_ci_clamped_to_zero_for_positive_series(self):
        r = fc.forecast(TREND, horizon=14, confidence=0.95)
        assert all(p["lower"] >= 0 for p in r["forecast"])
        assert all(p["upper"] >= p["lower"] for p in r["forecast"])

    def test_describe_returns_text(self):
        assert fc.describe("linear")
        assert fc.describe("unknown")  # defensive fallback

    def test_dates_parsed_and_future_dated(self):
        import datetime
        start = datetime.date(2026, 1, 1)
        dates = [(start + datetime.timedelta(days=i)).isoformat() for i in range(30)]
        r = fc.forecast([float(i) for i in range(30)], dates=dates, horizon=5)
        assert r["forecast"][0]["date"].strftime("%Y-%m-%d") == "2026-01-31"


SALES_CSV = (
    "sale_date,amount,payment_method\n"
    "2026-06-01,12.5,cash\n"
    "2026-06-02,20.0,card\n"
    "2026-06-03,15.0,cash\n"
    "2026-06-04,22.5,card\n"
    "2026-06-05,18.0,card\n"
    "2026-06-06,9.0,cash\n"
    "2026-06-07,11.0,cash\n"
    "2026-06-08,13.5,card\n"
    "2026-06-09,21.0,cash\n"
    "2026-06-10,16.5,card\n"
    "2026-06-11,24.0,cash\n"
    "2026-06-12,19.5,card\n"
)


class TestEndpoints:
    def _upload_sales(self, client):
        res = client.post(
            "/api/v1/analyze",
            headers=api_headers(),
            files={"files": ("sales.csv", io.BytesIO(SALES_CSV.encode()), "text/csv")},
            data={"application_name": "SalesApp"},
        )
        assert res.status_code == 200, res.text
        return res.json()["report"]["files"][0]["file_id"]

    def test_preview_reports_columns_and_rows(self, client):
        fid = self._upload_sales(client)
        r = client.get(f"/api/v1/files/{fid}/preview", headers=api_headers())
        assert r.status_code == 200
        data = r.json()
        assert data["row_count"] == 12
        names = {c["name"] for c in data["columns"]}
        assert {"sale_date", "amount", "payment_method"} <= names
        assert data["recommended"]["date_col"] == "sale_date"
        assert data["recommended"]["value_col"] == "amount"

    def test_series_builds_daily_sums(self, client):
        fid = self._upload_sales(client)
        r = client.get(f"/api/v1/files/{fid}/series", headers=api_headers())
        assert r.status_code == 200
        data = r.json()
        assert data["meta"]["points"] == 12
        assert data["series"][0]["date"] == "2026-06-01"
        assert set(data["series"][0].keys()) == {"date", "value"}

    def test_forecast_endpoint(self, client):
        fid = self._upload_sales(client)
        r = client.get(
            f"/api/v1/files/{fid}/forecast",
            params={"horizon": 7, "confidence": 0.9},
            headers=api_headers(),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["forecast"]) == 7
        assert data["meta"]["value_column"] == "amount"
        assert all(p["upper"] >= p["lower"] for p in data["forecast"])
        assert data["method_description"]

    def test_count_agg_when_value_col_ignored(self, client):
        fid = self._upload_sales(client)
        r = client.get(
            f"/api/v1/files/{fid}/forecast",
            params={"value_col": "payment_method"},
            headers=api_headers(),
        )
        assert r.status_code == 200
        assert r.json()["meta"]["agg"] == "count"

    def test_no_date_column_returns_400(self, client):
        res = client.post(
            "/api/v1/analyze",
            headers=api_headers(),
            files={"files": ("p.csv", io.BytesIO(b"name,amount\nA,1\nB,2\n"), "text/csv")},
            data={"application_name": "NoDate"},
        )
        fid = res.json()["report"]["files"][0]["file_id"]
        r = client.get(f"/api/v1/files/{fid}/forecast", headers=api_headers())
        assert r.status_code == 400
        assert "date" in r.json()["detail"].lower()

    def test_missing_file_returns_404(self, client):
        r = client.get(
            "/api/v1/files/00000000-0000-0000-0000-000000000000/forecast",
            headers=api_headers(),
        )
        assert r.status_code == 404

    def test_bad_horizon_rejected(self, client):
        fid = self._upload_sales(client)
        r = client.get(
            f"/api/v1/files/{fid}/forecast",
            params={"horizon": 0},
            headers=api_headers(),
        )
        assert r.status_code == 422