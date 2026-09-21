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
    if isinstance(products, list):
        out["bars"].append({
            "title": "Top products by {amount}",
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
    if isinstance(daily, list):
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