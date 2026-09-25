"""Tests for the calculated-insight layer and the two aggregation bug fixes."""
from __future__ import annotations

import pandas as pd
import pytest

from app.analytics_service import classification, insights
from app.analytics_service.engines import sales
from app.analytics_service.reporting import (
    _insight_evidence_rows,
    _interpret_domain,
    build_insight_model,
    build_report_model,
)
from app.routers.analyses import TEMPLATES


def _fmap(df: pd.DataFrame) -> dict:
    scored = classification.classify_dataframe(df)["field_scores"]
    return {f: i["suggested_mapping"] for f, i in scored.items() if i.get("suggested_mapping")}


def _by_key(results: list[dict]) -> dict[str, dict]:
    return {r["key"]: r for r in results}


@pytest.fixture
def product_file() -> pd.DataFrame:
    """One row per product: row totals plus per-unit price and stock."""
    return pd.DataFrame(
        {
            "product_name": ["A", "B", "C", "D"],
            "total_revenue": [100.0, 200.0, 300.0, 400.0],
            "total_cost": [70.0, 160.0, 150.0, 400.0],
            "net_profit": [30.0, 40.0, 150.0, 0.0],
            "selling_price": [10.0, 20.0, 30.0, 40.0],
            "quantity_sold": [10.0, 10.0, 10.0, 10.0],
            "stock_balance": [5.0, 50.0, 10.0, 0.0],
        }
    )


@pytest.fixture
def dated_file() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=120, freq="D")
    values = [100.0 + (i % 7) * 5 for i in range(120)]
    return pd.DataFrame(
        {
            "sale_date": dates.strftime("%Y-%m-%d"),
            "total_amount": values,
            "quantity": [1] * 120,
            "stock_on_hand": [10] * 120,
        }
    )


# --- profitability ---------------------------------------------------------


def test_gross_margin_uses_row_totals_not_unit_math(product_file):
    res = _by_key(insights.compute_insights(product_file, _fmap(product_file)))
    margin = res["gross_margin_pct"]
    assert margin["status"] == "ok"
    assert margin["unit"] == "percent"
    assert margin["value"] == pytest.approx(22.0, abs=0.05)
    assert margin["evidence"]["basis"] == "row totals"


def test_gross_margin_falls_back_to_unit_economics():
    df = pd.DataFrame(
        {
            "unit_price": [10.0, 20.0],
            "unit_cost": [6.0, 15.0],
            "quantity": [10.0, 10.0],
        }
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    assert res["gross_margin_pct"]["value"] == pytest.approx(30.0, abs=0.05)
    assert res["gross_margin_pct"]["evidence"]["basis"] == "unit economics"


def test_net_profit_and_loss_making_rows(product_file):
    res = _by_key(insights.compute_insights(product_file, _fmap(product_file)))
    assert res["net_profit_total"]["value"] == pytest.approx(220.0)
    assert res["loss_making_products"]["status"] == "ok"


def test_loss_making_detects_negative_profit():
    df = pd.DataFrame({"net_profit": [10.0, -5.0, -3.0], "product_name": ["a", "b", "c"]})
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    losing = res["loss_making_products"]
    assert losing["value"] == 2
    assert losing["evidence"]["loss_value"] == pytest.approx(-8.0)


# --- concentration ---------------------------------------------------------


def test_pareto_reports_count_reaching_eighty_percent(product_file):
    res = _by_key(insights.compute_insights(product_file, _fmap(product_file)))
    pareto = res["pareto_80"]
    assert pareto["status"] == "ok"
    assert 1 <= pareto["value"] <= 4
    assert pareto["evidence"]["share_80"] == pytest.approx(90.0)
    assert pareto["evidence"]["total_rows"] == 4


def test_hhi_flags_concentration():
    spread = pd.DataFrame({"total_amount": [1.0] * 50})
    assert _by_key(insights.compute_insights(spread, {}))["revenue_concentration"]["value"] < 1500
    tight = pd.DataFrame({"total_amount": [100.0, 1.0, 1.0, 1.0]})
    assert _by_key(insights.compute_insights(tight, {}))["revenue_concentration"]["value"] > 1500


# --- waste / stock risk ----------------------------------------------------


def test_dead_stock_counts_unsold_stock(product_file):
    res = _by_key(insights.compute_insights(product_file, _fmap(product_file)))
    dead = res["dead_stock_count"]
    assert dead["status"] == "ok"
    assert dead["value"] == 0


def test_dead_stock_found_when_sales_are_zero():
    df = pd.DataFrame(
        {"stock_on_hand": [5, 0, 7], "quantity": [0, 3, 0], "product_name": ["a", "b", "c"]}
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    dead = res["dead_stock_count"]
    assert dead["value"] == 2
    assert dead["evidence"]["units"] == pytest.approx(12.0)


def test_overstock_flags_three_times_ratio():
    df = pd.DataFrame(
        {"stock_on_hand": [100, 1], "quantity": [1, 10], "product_name": ["a", "b"]}
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    over = res["overstocked_products"]
    assert over["value"] == 1
    assert over["evidence"]["top"][0]["product"] == "a"


def test_stock_turnover_flags_slow_movement():
    df = pd.DataFrame({"stock_on_hand": [100], "quantity": [10]})
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    assert res["stock_turnover"]["value"] == pytest.approx(0.1)
    assert res["stock_turnover"]["severity"] == "bad"


def test_tied_up_capital_never_values_stock_with_a_row_total():
    df = pd.DataFrame(
        {
            "stock_on_hand": [10.0, 10.0],
            "total_cost": [100.0, 200.0],
            "quantity": [10.0, 10.0],
        }
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    tied = res["tied_up_capital"]
    assert tied["value"] == pytest.approx(300.0)
    assert "implied" in tied["evidence"]["cost_basis"]


def test_expiry_buckets_split_expired_and_upcoming():
    from datetime import date, timedelta

    today = date.today()
    df = pd.DataFrame(
        {
            "expiry_date": [
                (today - timedelta(days=10)).isoformat(),
                (today + timedelta(days=20)).isoformat(),
                (today + timedelta(days=200)).isoformat(),
            ],
            "stock_on_hand": [1, 2, 3],
        }
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    expiry = res["expiry_at_risk"]
    assert expiry["status"] == "ok"
    assert expiry["evidence"]["buckets"]["expired"] == 1
    assert expiry["evidence"]["buckets"]["30"] == 1
    assert expiry["value"] == 1


def test_waste_rate_from_waste_quantity():
    df = pd.DataFrame(
        {
            "waste_quantity": [0, 3, 2],
            "unit_cost": [10.0, 10.0, 10.0],
            "total_amount": [1000.0, 0.0, 0.0],
        }
    )
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    waste = res["waste_rate"]
    assert waste["status"] == "ok"
    assert waste["value"] == pytest.approx(5.0)
    assert waste["evidence"]["value"] == pytest.approx(50.0)
    assert waste["evidence"]["rate_pct"] == pytest.approx(5.0)


def test_waste_detected_from_event_type_rows():
    df = pd.DataFrame({"event_type": ["sale", "EXPIRED", "write-off", "sale"]})
    res = _by_key(insights.compute_insights(df, _fmap(df)))
    assert res["waste_rate"]["evidence"]["rows"] == 2


# --- trends ----------------------------------------------------------------


def test_revenue_trend_detects_direction(dated_file):
    res = _by_key(insights.compute_insights(dated_file, _fmap(dated_file)))
    trend = res["revenue_trend"]
    assert trend["status"] == "ok"
    assert trend["unit"] == "percent"
    assert trend["evidence"]["periods"] >= 3
    assert trend["evidence"]["direction"] in {"up", "down", "flat"}


def test_trend_skipped_without_date_column(product_file):
    res = _by_key(insights.compute_insights(product_file, _fmap(product_file)))
    for key in ("revenue_trend", "volume_trend"):
        assert res[key]["status"] == "skipped"
        assert res[key]["value"] is None
        assert "sale_timestamp" in res[key]["missing_columns"]


def test_trend_skipped_when_too_little_history():
    df = pd.DataFrame({"sale_date": ["2026-01-01", "2026-01-02"], "total_amount": [1.0, 2.0]})
    res = _by_key(insights.compute_insights(df, {}))
    assert res["revenue_trend"]["status"] == "skipped"
    assert "at least 3" in res["revenue_trend"]["detail"]


# --- fault tolerance -------------------------------------------------------


def test_empty_dataframe_yields_all_skipped_not_errors():
    res = _by_key(insights.compute_insights(pd.DataFrame(), {}))
    assert res, "rules should still be reported"
    for key, item in res.items():
        assert item["status"] in {"skipped", "ok"}, key
        assert item["severity"] in {"muted", "good", "warn", "bad", "info"}


def test_missing_columns_are_named_in_detail():
    res = _by_key(insights.compute_insights(pd.DataFrame({"foo": [1, 2]}), {}))
    expiry = res["expiry_at_risk"]
    assert expiry["status"] == "skipped"
    assert "expiry_date" in expiry["missing_columns"]
    assert "expiry" in expiry["detail"].lower()


def test_a_raising_rule_is_reported_as_error_not_raised(monkeypatch):
    from app.analytics_service.registry import insight_rule, insight_rules

    @insight_rule("explodes", family="general", order=999)
    def _boom(ctx):
        raise RuntimeError("kaboom")

    try:
        res = _by_key(insights.compute_insights(pd.DataFrame({"a": [1]}), {}))
        assert res["explodes"]["status"] == "error"
        assert "kaboom" in res["explodes"]["detail"]
    finally:
        insight_rules._entries.pop("explodes", None)


def test_every_result_is_json_safe(dated_file):
    import json

    payload = insights.compute_insights(dated_file, _fmap(dated_file))
    json.dumps(payload)


# --- sales engine bug fixes ------------------------------------------------


def test_sales_daily_series_carries_value_not_only_count(dated_file):
    out = sales.sales_analytics(dated_file, _fmap(dated_file))
    series = out["daily_series"]
    assert series
    assert "count" in series[0] and "value" in series[0]
    assert sum(point["value"] for point in series) == pytest.approx(
        float(dated_file["total_amount"].sum())
    )


def test_sales_margin_uses_row_totals_when_present(product_file):
    out = sales.sales_analytics(product_file, _fmap(product_file))
    assert out["margin_basis"] == "row totals"
    assert out["gross_margin"] == pytest.approx(220.0)
    assert out["gross_margin_pct"] == pytest.approx(22.0, abs=0.05)


def test_sales_margin_never_multiplies_quantity_by_a_row_total(product_file):
    out = sales.sales_analytics(product_file, _fmap(product_file))
    assert abs(out["gross_margin"]) < 1_000_000


def test_sales_margin_pct_reported_for_unit_economics_basis():
    df = pd.DataFrame(
        {"unit_price": [10.0, 20.0], "unit_cost": [6.0, 15.0], "quantity": [10.0, 10.0]}
    )
    out = sales.sales_analytics(df, _fmap(df))
    assert out["margin_basis"] == "unit economics"
    assert out["gross_margin"] == pytest.approx(90.0)
    assert out["gross_margin_pct"] == pytest.approx(30.0)


# --- reporting model -------------------------------------------------------


def test_insight_model_groups_and_keeps_skipped_notes(dated_file):
    model = build_insight_model(insights.compute_insights(dated_file, _fmap(dated_file)))
    assert model["total"] > 0
    titles = [g["title"] for g in model["groups"]]
    assert "Profitability" in titles or "Trends" in titles
    for group in model["groups"]:
        for item in group["items"]:
            assert item["value"] != ""
            assert item["severity"] in {"good", "warn", "bad", "info"}
    for note in model["notes"]:
        assert note["detail"]


def test_insight_model_handles_absent_insights():
    model = build_insight_model(None)
    assert model["groups"] == [] and model["notes"] == [] and model["total"] == 0


def test_evidence_rows_render_scalars_with_thousands_separators():
    rows = _insight_evidence_rows({"revenue": 60771.96, "rows": 42, "basis": "revenue - cost"})
    assert rows == [
        {"label": "revenue", "value": "60,771.96"},
        {"label": "rows", "value": "42"},
        {"label": "basis", "value": "revenue - cost"},
    ]


def test_evidence_rows_never_render_scientific_notation():
    for value in (1234567.0, 60771.96, 0.5, -19513.08):
        rendered = _insight_evidence_rows({"x": value})[0]["value"]
        assert "e+" not in rendered and "e-" not in rendered
    assert _insight_evidence_rows({"x": 1234567.0})[0]["value"] == "1,234,567"
    assert _insight_evidence_rows({"x": 0.0})[0]["value"] == "0"


def test_evidence_rows_keep_magnitudes_two_decimals_would_zero_out():
    assert _insight_evidence_rows({"x": 0.0000123})[0]["value"] == "1.23e-05"
    assert _insight_evidence_rows({"x": -0.0004})[0]["value"] == "-0.0004"


def test_evidence_rows_render_top_products_compactly():
    rows = _insight_evidence_rows(
        {"top": [{"product": f"P{i}", "profit": -float(i)} for i in range(1, 5)]},
        "loss_making_products",
    )
    assert rows == [{"label": "worst", "value": "P1 (-1.00), P2 (-2.00), P3 (-3.00)"}]


def test_pareto_top_list_is_not_labelled_worst():
    rows = _insight_evidence_rows(
        {"top": [{"label": "103", "value": 198.86}]}, "pareto_80"
    )
    assert rows == [{"label": "top contributors", "value": "103 (198.86)"}]


def test_evidence_rows_render_expiry_buckets():
    rows = _insight_evidence_rows({"buckets": {"expired": 2, "30": 1}})
    assert rows == [{"label": "buckets", "value": "expired 2 · 30d 1"}]


def test_evidence_rows_drop_series_and_nested_dicts():
    rows = _insight_evidence_rows(
        {"direction": "down", "series": [{"t": 1}], "meta": {"a": 1}}
    )
    assert rows == [{"label": "direction", "value": "down"}]


def test_evidence_rows_tolerate_missing_or_malformed_evidence():
    assert _insight_evidence_rows(None) == []
    assert _insight_evidence_rows("nope") == []
    assert _insight_evidence_rows({}) == []
    # structured keys holding the wrong type degrade to a plain scalar
    assert _insight_evidence_rows({"top": "not a list"}) == [
        {"label": "top", "value": "not a list"}
    ]
    assert _insight_evidence_rows({"buckets": 5}) == [
        {"label": "buckets", "value": "5"}
    ]


def test_report_html_renders_evidence_for_calculated_insights(dated_file):
    payload = {
        "application": {"id": "a", "name": "app", "created_at": None},
        "analysis": {"id": "b", "status": "completed", "created_at": None,
                     "completed_at": None},
        "model": build_report_model(
            {
                "files": [{
                    "filename": "f.csv",
                    "insights": insights.compute_insights(
                        dated_file, _fmap(dated_file)
                    ),
                }],
            },
            {},
        ),
        "report": {},
    }
    html = TEMPLATES.get_template("report.html").render(**payload)
    assert "Calculated insights" in html
    assert 'class="evidence"' in html


# --- chart titles / series choice -------------------------------------------


def test_top_products_chart_names_the_amount_column_it_used():
    out = _interpret_domain({
        "amount_field": "total_revenue",
        "top_products": [{"product": "A", "amount": 10.0}],
    })
    title = out["bars"][0]["title"]
    assert title == "Top products by total revenue"
    assert "{" not in title, "unformatted placeholder leaked into a chart title"


def test_top_products_chart_falls_back_when_amount_field_is_absent():
    out = _interpret_domain({"top_products": [{"product": "A", "amount": 10.0}]})
    assert out["bars"][0]["title"] == "Top products by revenue"


def test_daily_series_charts_the_value_when_the_engine_supplied_one():
    out = _interpret_domain({
        "amount_field": "revenue",
        "daily_series": [{"date": "d1", "count": 3, "value": 120.0},
                         {"date": "d2", "count": 2, "value": 80.0}],
    })
    assert out["bars"][0]["title"] == "Daily series (revenue)"
    assert [b["value"] for b in out["bars"][0]["bars"]] == [120.0, 80.0]


def test_daily_series_falls_back_to_count_without_usable_values():
    for series in ([{"date": "d", "count": 3}],
                   [{"date": "d", "count": 3, "value": 0}],
                   [{"date": "d", "count": 3, "value": None}]):
        out = _interpret_domain({"daily_series": series})
        assert out["bars"][0]["title"] == "Daily series (count)"


def test_amount_field_never_becomes_a_kpi():
    out = _interpret_domain({"amount_field": "total_revenue", "revenue": 100.0})
    labels = [k["label"] for k in out["kpis"]]
    assert labels == ["Revenue"]
