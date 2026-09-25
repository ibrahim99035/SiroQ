"""Report engine: turns a persisted analysis report + summary into a flat,
template-friendly display model (KPIs, normalized bars, tables, findings).

The template stays logic-free; every computation lives here so the HTML report
is deterministic and unit-testable.
"""
from __future__ import annotations

from typing import Any


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _pairs(items: list, label_key: str, value_key: str) -> list[dict]:
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        val = it.get(value_key)
        if val is None:
            continue
        out.append({"label": str(it.get(label_key, "")), "value": float(val)})
    return out


def bars(items: list[dict], max_len: int = 12) -> list[dict]:
    """Normalize ``[{label, value}]`` to display bars with 0-100 widths."""
    taken = items[:max_len]
    vals = [it["value"] for it in taken if it["value"] is not None]
    mx = max(vals) if vals else 1.0
    return [
        {
            "label": it["label"],
            "value": round(float(it["value"]), 2),
            "pct": round(100.0 * it["value"] / mx, 1) if mx else 0.0,
        }
        for it in taken
        if it["value"] is not None
    ]


def _dict_bars(d: dict, max_len: int = 12) -> list[dict]:
    counted: dict[str, float] = {}
    for k, v in d.items():
        if _num(v):
            counted[str(k)] = float(v)
    return bars([{"label": k, "value": v} for k, v in counted.items()], max_len)


def _list_bars(seq: list, max_len: int = 12) -> list[dict]:
    counted: dict[str, float] = {}
    for it in seq:
        if isinstance(it, dict) and _num(it.get("value")):
            counted[str(it.get("value", ""))] = float(it["value"])
        elif isinstance(it, dict) and _num(it.get("count")) and "value" in it:
            counted[str(it["value"])] = float(it["count"])
    return bars([{"label": k, "value": v} for k, v in counted.items()], max_len)


def _interpret_domain(da: Any) -> dict:
    """Split a domain-analytics blob into kpis + bars + tables + skipped."""
    out: dict[str, Any] = {"kpis": [], "bars": [], "tables": [], "skipped": []}
    if not isinstance(da, dict):
        out["kpis"] = [{"label": "category", "value": str(da)}]
        return out

    skipped = da.get("skipped")
    if isinstance(skipped, list):
        out["skipped"] = [str(s) for s in skipped]

    consumed = {"skipped"}
    scalar_keys = set()

    # Hand-shaped series.
    products = da.get("top_products")
    consumed.add("amount_field")
    if isinstance(products, list):
        # named from the amount column the engine actually aggregated
        amount_field = da.get("amount_field") or "revenue"
        out["bars"].append({
            "title": f"Top products by {str(amount_field).replace('_', ' ')}",
            "bars": bars(_pairs(products, "product", "amount")),
        })
        consumed.add("top_products")

    mix = da.get("payment_mix")
    if isinstance(mix, list):
        out["bars"].append({
            "title": "Payment mix",
            "bars": bars(_pairs(mix, "method", "amount")),
        })
        consumed.add("payment_mix")

    daily = da.get("daily_series")
    if isinstance(daily, list) and daily:
        # daily_series carries both count and a summed value; prefer the value
        # for sales, where revenue matters more than row tally.
        has_value = all(
            isinstance(d, dict) and _num(d.get("value")) and d.get("value")
            for d in daily
        )
        if has_value:
            out["bars"].append({
                "title": f"Daily series ({da.get('amount_field') or 'amount'})",
                "bars": bars(_pairs(daily, "date", "value"), 20),
            })
        else:
            out["bars"].append({
                "title": "Daily series (count)",
                "bars": bars(_pairs(daily, "date", "count"), 20),
            })
        consumed.add("daily_series")

    iph = da.get("inventory_profile_headers") or da.get("categorical_flags")
    if isinstance(iph, list):
        out["bars"].append({
            "title": "Flagged rows",
            "bars": bars(_pairs(iph, "flag", "count")),
        })
        consumed.add("inventory_profile_headers")
        consumed.add("categorical_flags")

    for key, val in da.items():
        if key in consumed:
            continue
        if isinstance(val, dict) and any(_num(v) for v in val.values()):
            out["bars"].append({
                "title": key.replace("_", " ").title(),
                "bars": _dict_bars(val),
            })
            consumed.add(key)
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            headers = list(val[0].keys())
            if {*headers} == {"value", "count"}:
                out["bars"].append({
                    "title": key.replace("_", " ").title(),
                    "bars": bars([{"label": r["value"], "value": r["count"]} for r in val]),
                })
                consumed.add(key)
            else:
                out["tables"].append({
                    "title": key.replace("_", " ").title(),
                    "headers": headers,
                    "rows": [[rfmt(v) for v in row.values()] for row in val],
                })
                consumed.add(key)
        elif _num(val):
            scalar_keys.add(key)

    for key in sorted(scalar_keys):
        out["kpis"].append({"label": key.replace("_", " ").title(), "value": _fmt(da[key])})
    return out


def rfmt(v: Any) -> str:
    return _fmt(v) if _num(v) else str(v)


def _profile_rows(profile: Any) -> list[dict]:
    if not isinstance(profile, dict) or not profile.get("column_profiles"):
        return []
    rows = []
    for col in profile["column_profiles"]:
        row: dict[str, Any] = {
            "name": col.get("name"),
            "dtype": col.get("dtype"),
            "kind": col.get("kind"),
            "null_pct": _fmt(col.get("null_pct", 0)),
            "unique_pct": _fmt(col.get("unique_pct", 0)),
            "stats": "",
        }
        if col.get("kind") == "number":
            pieces = [
                f"min {_fmt(col['min'])}",
                f"mean {_fmt(col['mean'])}",
                f"max {_fmt(col['max'])}",
            ]
            if col.get("sum") is not None:
                pieces.append(f"sum {_fmt(col['sum'])}")
            row["stats"] = "; ".join(pieces)
        elif col.get("kind") == "date":
            span = col.get("span_days")
            row["stats"] = (
                f"{col.get('min')} → {col.get('max')}"
                + (f" ({span:,} days)" if span is not None else "")
            )
        top = col.get("top_values") or []
        row["top_values"] = ", ".join(
            f"{t.get('value')} × {t.get('count')}" for t in top[:3]
        )
        rows.append(row)
    return rows


INSIGHT_FAMILY_TITLES = {
    "profitability": "Profitability",
    "concentration": "Concentration",
    "waste": "Waste & stock risk",
    "trend": "Trends",
    "general": "Insights",
}

FAMILY_ORDER = ["profitability", "trend", "concentration", "waste", "general"]


TOP_LIST_LABELS = {
    # a loss/overstock list is ordered worst-first; a Pareto list is the
    # biggest contributors, so calling it "worst" would misread the evidence
    "loss_making_products": "worst",
    "overstocked_products": "worst",
    "pareto_80": "top contributors",
}


def _insight_evidence_rows(evidence: Any, insight_key: str | None = None) -> list[dict[str, str]]:
    """Flatten one insight's ``evidence`` into printable label/value rows.

    Scalars pass through. The two structured shapes rules emit get a compact
    rendering (``top`` -> "a (1), b (2)"; ``buckets`` -> "expired 1, 30d 2").
    ``series`` is dropped: a full trend series is a chart, not a table cell.
    """
    if not isinstance(evidence, dict):
        return []
    rows: list[dict[str, str]] = []
    for key, value in evidence.items():
        if key in {"series", "products"}:
            continue
        label = key.replace("_", " ")
        if key == "buckets" and isinstance(value, dict):
            parts = [
                f"{name}d {count}" if name.isdigit() else f"{name} {count}"
                for name, count in value.items()
            ]
            if parts:
                rows.append({"label": label, "value": " · ".join(parts)})
            continue
        if key == "top" and isinstance(value, list):
            parts = []
            for entry in value[:3]:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("product") or entry.get("month") or entry.get("label")
                if name is None:
                    continue
                figure = next(
                    (entry[k] for k in ("value", "profit", "excess", "ratio")
                     if isinstance(entry.get(k), (int, float))),
                    None,
                )
                parts.append(f"{name} ({figure:,.2f})" if isinstance(figure, (int, float))
                             else str(name))
            if parts:
                rows.append({
                    "label": TOP_LIST_LABELS.get(insight_key or "", "worst"),
                    "value": ", ".join(parts),
                })
            continue
        if isinstance(value, dict):
            continue
        if value is None or value == "":
            continue
        if isinstance(value, float):
            # plain decimal notation: 1.235e+06 is unreadable in a report
            if value != 0 and abs(value) < 0.01:
                rendered = f"{value:,.6g}"  # keep magnitudes 2dp would zero out
            else:
                rendered = f"{value:,.2f}".rstrip("0").rstrip(".")
                if rendered in {"", "-", "-0"}:
                    rendered = "0"
        elif isinstance(value, int):
            rendered = f"{value:,}"
        else:
            rendered = str(value)
        rows.append({"label": label, "value": rendered})
    return rows


def _insight_value(item: dict) -> str:
    """Format one insight's value with its unit for display."""
    val = item.get("value")
    if val is None:
        return "—"
    unit = item.get("unit")
    if unit == "percent":
        return f"{float(val):.2f}%"
    if unit == "currency":
        return f"{float(val):,.2f}"
    if unit == "ratio":
        return f"{float(val):.2f}x"
    if unit in {"index"}:
        return f"{float(val):,.0f}"
    if unit in {"count", "rows", "products", "units"}:
        return f"{float(val):,.0f}"
    if isinstance(val, (int, float)):
        return f"{val:,.2f}"
    return str(val)


def build_insight_model(insights: Any) -> dict:
    """Group calculated insights by family, ready for the template.

    Rules that could not run are kept (never dropped) under ``notes`` so the
    report states which columns each missing insight needs instead of silently
    omitting it.
    """
    groups: list[dict] = []
    items = [i for i in (insights or []) if isinstance(i, dict)]
    by_family: dict[str, list[dict]] = {}
    notes: list[dict] = []
    for item in items:
        status = item.get("status")
        if status == "ok":
            by_family.setdefault(item.get("family") or "general", []).append(item)
        else:
            notes.append({
                "label": item.get("label") or item.get("key"),
                "status": status or "skipped",
                "detail": item.get("detail") or "",
                "missing": ", ".join(item.get("missing_columns") or item.get("requires") or []),
            })
    for family in FAMILY_ORDER:
        rows = by_family.get(family)
        if not rows:
            continue
        groups.append({
            "title": INSIGHT_FAMILY_TITLES.get(family, family.title()),
            "items": [
                {
                    "label": r.get("label"),
                    "value": _insight_value(r),
                    "unit": r.get("unit"),
                    "severity": r.get("severity") or "info",
                    "detail": r.get("detail") or "",
                    "evidence": r.get("evidence") or {},
                    "evidence_rows": _insight_evidence_rows(r.get("evidence"), r.get("key")),
                }
                for r in rows
            ],
        })
    for family, rows in by_family.items():
        if family in FAMILY_ORDER:
            continue
        groups.append({
            "title": INSIGHT_FAMILY_TITLES.get(family, family.title()),
            "items": [
                {"label": r.get("label"), "value": _insight_value(r),
                 "unit": r.get("unit"), "severity": r.get("severity") or "info",
                 "detail": r.get("detail") or "", "evidence": r.get("evidence") or {},
                 "evidence_rows": _insight_evidence_rows(r.get("evidence"), r.get("key"))}
                for r in rows
            ],
        })
    return {"groups": groups, "notes": notes, "total": len(items)}


def build_file_model(f: Any) -> dict:
    model: dict[str, Any] = {
        "filename": f.get("filename"),
        "file_id": f.get("file_id"),
        "file_type": f.get("file_type"),
        "size_bytes": f.get("size_bytes"),
        "sha256": f.get("sha256"),
        "row_count": f.get("row_count"),
        "column_count": len(f.get("columns") or []),
        "columns": f.get("columns") or [],
        "notes": f.get("notes") or [],
        "errors": f.get("errors") or [],
        "top_category": f.get("top_category"),
        "multi_sheet": f.get("multi_sheet", False),
        "sheet_categories": f.get("sheet_categories") or [],
    }
    if f.get("multi_sheet") and f.get("sheets"):
        model["rows"] = [
            {"sheet": s.get("sheet"), "rows": s.get("row_count"),
             "top_category": s.get("top_category")}
            for s in f["sheets"] if isinstance(s, dict)
        ]
    else:
        model["rows"] = [{"sheet": None, "rows": f.get("row_count"),
                          "top_category": f.get("top_category")}]

    cats = f.get("categories") or {}
    model["category_bars"] = bars(
        [{"label": k, "value": v} for k, v in cats.items()], max_len=10
    )

    dq = f.get("data_quality") or {}
    model["quality_score"] = dq.get("score")
    model["duplicate_count"] = dq.get("duplicate_count")
    model["checks"] = [
        {"check": c.get("check"), "status": c.get("status")}
        for c in (dq.get("checks") or [])
    ]
    model["findings"] = f.get("quality_findings") or []

    model["profile_rows"] = _profile_rows(f.get("profile"))

    da = f.get("domain_analytics")
    if da is not None:
        model["domain"] = _interpret_domain(da)
    model["insights"] = build_insight_model(f.get("insights"))
    return model


def build_report_model(report: Any, summary: Any = None) -> dict:
    report = report or {}
    model: dict[str, Any] = {
        "application_name": report.get("application_name"),
        "application_id": report.get("application_id"),
        "analyzed_at": report.get("analyzed_at"),
        "engine_version": report.get("engine_version"),
        "files": [build_file_model(f) for f in (report.get("files") or [])],
    }
    if summary:
        model["summary"] = {
            "file_count": summary.get("file_count"),
            "total_rows": summary.get("total_rows"),
            "quality_score": summary.get("data_quality_score"),
            "findings_count": summary.get("findings_count"),
            "categories_detected": summary.get("categories_detected") or {},
        }
    return model