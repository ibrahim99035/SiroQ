"""Report engine: turns a persisted analysis report + summary into a flat,
template-friendly display model (KPIs, normalized bars, tables, findings).

The template stays logic-free; every computation lives here so the HTML report
is deterministic and unit-testable.
"""
from __future__ import annotations

from typing import Any

from app.analytics_service.forecast import describe as forecast_describe


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
        # the engine may key a column as ``column`` or ``name`` depending on
        # whether it came from the profile or a sheet-level projection
        name = col.get("name") or col.get("column")
        row: dict[str, Any] = {
            "name": name,
            "dtype": col.get("dtype"),
            "kind": col.get("kind"),
            "null_pct": _fmt(col.get("null_pct", 0)),
            "unique_pct": _fmt(col.get("unique_pct", 0)),
            "stats": "",
        }
        # The engine only emits min/mean/max when a column actually holds values
        # (profile.py:55), so an all-null column is kind "number" with none of
        # them present. Subscripting those keys raised KeyError and took down the
        # whole report for one empty column; the rate it does carry, 100% null,
        # is the finding.
        if col.get("kind") == "number":
            labels = (("min", "min"), ("mean", "mean"), ("max", "max"))
            pieces = [
                f"{label} {_fmt(col[key])}"
                for key, label in labels
                if col.get(key) is not None
            ]
            if col.get("sum") is not None:
                pieces.append(f"sum {_fmt(col['sum'])}")
            row["stats"] = "; ".join(pieces)
        elif col.get("kind") == "date":
            span = col.get("span_days")
            if col.get("min") is None or col.get("max") is None:
                row["stats"] = ""
            else:
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


# The per-column measures that get their own grid. Shared by the table builder and
# the detail filter below, because the two must agree: the detail map carries what
# the table omits, so a key that is neither shown nor omitted is either duplicated
# on the page or silently lost.
PROFILE_TABLE_KEYS = ("Type", "Nulls", "Unique")


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
        "row_filter": f.get("row_filter"),
        # Carried so `_file_projection` can emit the forecast node. Omitted here
        # would leave the whole forecasting stage invisible to the report.
        "forecast": f.get("forecast"),
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


#: Schema version stamped into every projection.
#:
#: Version `v1` covers the *tagged-node* extension to the label->value tree (see
#: `docs/CLIENT_REPORT_CONTRACT.md`): `$chart`, `$forecast` and `$notes`. Those
#: tags are additive, so a document written by this engine still renders as a
#: plain tree on a panel that predates them, and a document without them still
#: renders as a plain tree here. That is why the version did not change when the
#: tags were added -- a bump would be a claim of incompatibility that does not
#: exist, and the client's `KNOWN_SCHEMA_VERSION` drift warning is only useful
#: while it stays quiet for documents we actually do render.
CLIENT_SCHEMA_VERSION = "siroq.client.v1"


def _quality_verdict(score: Any, failed: int) -> str:
    """Turn a quality score into the word a reviewer acts on."""
    if not _num(score):
        return "Not scored"
    s = float(score)
    if failed:
        return "Action required"
    if s >= 90:
        return "Strong"
    if s >= 75:
        return "Acceptable"
    if s >= 60:
        return "Review before accepting"
    return "Action required"


def _agg_severity(models: list[dict]) -> str:
    """Highest severity across all files drives the headline.

    Takes *file models*, whose ``insights`` are already grouped by
    :func:`build_insight_model`. Re-running the grouper here returned empty
    groups for that reason, so the headline said "Info" for every application
    regardless of what the engine actually flagged.
    """
    rank = {"critical": 4, "warning": 3, "warn": 3, "info": 1}
    worst = "info"
    for m in models:
        for group in (m.get("insights") or {}).get("groups", []):
            for item in group.get("items", []):
                sev = str(item.get("severity") or "info").lower()
                if rank.get(sev, 0) > rank.get(worst, 0):
                    worst = sev
    return worst


def _bar_lines(bar_group: dict) -> list[str]:
    """Flatten a `{title, bars[]}` group into 'label — value' strings.

    Kept for the printable report and the dashboard, which render text. The
    client projection uses `_bar_chart` so it can draw the same data.
    """
    return [f"{b['label']} — {_fmt(b['value'])}" for b in bar_group.get("bars", [])]


# Number of bars to send to the client. The printable report and dashboard cap at
# the same 12, and a chart with 40 slivers is less readable than a table would be.
CLIENT_BAR_MAX = 12


def _bar_chart(bar_group: dict) -> dict[str, Any] | None:
    """A `{title, bars[]}` group as a tagged node the client renders as a chart.

    The projection is a recursive label->value tree, so a chart has to arrive as
    an ordinary object. Tagging it with ``$chart`` lets the client renderer swap
    in an SVG without the service knowing anything about the client's internals,
    and a client that predates the tag still renders the object as plain fields
    rather than failing.
    """
    taken = [b for b in (bar_group.get("bars") or []) if b.get("label")][:CLIENT_BAR_MAX]
    if len(taken) < 2:
        # One bar is not a chart. Leave it to the caller's flat rendering.
        return None
    return {
        "$chart": "bar",
        "Total": _fmt(sum(float(b.get("value") or 0) for b in taken)),
        "Bars": [
            {
                "Label": str(b["label"]),
                "Value": _fmt(b.get("value")),
                "Share": f"{float(b.get('pct') or 0):.1f}%",
            }
            for b in taken
        ],
    }


# Points of history to send with a forecast. The engine keeps at most 24; the
# projection is stored as jsonb and read on every panel open, and a chart of 24
# points at ~7px each already fills its width.
CLIENT_FORECAST_POINTS = 18


def _forecast_node(fc: dict | None) -> dict[str, Any] | None:
    """A forecast as a tagged node the client draws as history + projection.

    Carries the confidence interval and the method that won, because a curve
    without them invites exactly the reading the projection exists to prevent:
    a point estimate presented as a promise. When the fit was poor the notes say
    so in the panel itself rather than leaving it to the reader to infer from a
    wiggly line.
    """
    if not isinstance(fc, dict):
        return None
    series = [p for p in (fc.get("series") or []) if _num(p.get("value"))]
    projected = [p for p in (fc.get("forecast") or []) if _num(p.get("value"))]
    if not series or not projected:
        return None

    def _day(value: Any) -> str:
        text = str(value)[:10]
        return text if len(text) == 10 else ""

    history = [
        {"Period": _day(p.get("date")), "Value": _fmt(p.get("value"))}
        for p in series[-CLIENT_FORECAST_POINTS:]
    ]
    future = [
        {
            "Period": _day(p.get("date")),
            "Value": _fmt(p.get("value")),
            "Low": _fmt(p.get("lower")),
            "High": _fmt(p.get("upper")),
        }
        for p in projected
    ]

    diag = fc.get("diagnostics") or {}
    method = fc.get("method")
    node: dict[str, Any] = {
        "$forecast": True,
        "Series": fc.get("series_label") or "Value",
        "Granularity": fc.get("granularity") or "period",
        "Method": method or "n/a",
        "Method note": forecast_describe(method) if method else "No method was selected.",
        "Confidence": f"{int(round(float(fc.get('confidence') or 0.9) * 100))}% interval",
        "Horizon": f"{len(projected)} {fc.get('granularity') or 'period'}s ahead",
        "History": history,
        "Projected": future,
    }

    # Accuracy, but only where the numbers mean something. r2 is meaningless on a
    # short or near-flat series, and MAPE explodes when the actuals approach zero.
    accuracy: dict[str, str] = {}
    if _num(diag.get("rmse")):
        accuracy["Error (RMSE)"] = _fmt(diag["rmse"])
    if _num(diag.get("mape")) and 0 <= float(diag["mape"]) < 1000:
        accuracy["Typical error"] = f"{float(diag['mape']):.1f}%"
    if _num(diag.get("r2")):
        accuracy["Fit (R²)"] = f"{float(diag['r2']):.2f}"
    if accuracy:
        node["Accuracy"] = accuracy

    notes = [str(n) for n in (diag.get("notes") or []) if n]
    if notes:
        node["Notes"] = notes
    return node


def _table_chart(table: dict) -> dict[str, Any] | None:
    """A `{title, columns[], rows[]}` table as a tagged node.

    Tables are already structured, so the client can lay them out as a real grid
    instead of one pre-joined string per row. Row and column counts are bounded
    because the projection is stored as jsonb and read on every panel open.
    """
    # `_interpret_domain` emits `headers` + list-of-lists `rows`; `_table_lines`
    # reads `columns` + list-of-dicts, so accept either rather than silently
    # dropping every table when only one producer is in play.
    cols = table.get("headers") or table.get("columns") or []
    cols = [str(c) for c in cols][:6]
    raw_rows = table.get("rows") or []
    rows: list[list[str]] = []
    for row in raw_rows[:25]:
        if isinstance(row, dict):
            cells = [rfmt(row.get(c)) for c in cols]
        elif isinstance(row, (list, tuple)):
            cells = [rfmt(c) for c in row][: len(cols)]
        else:
            continue
        if any(c not in (None, "") for c in cells):
            rows.append([str(c) for c in cells])
    if not cols or not rows:
        return None
    return {
        "$chart": "table",
        "Columns": cols,
        "Rows": rows,
    }


def _table_lines(table: dict) -> list[str]:
    """Flatten a `{title, columns, rows}` table into readable strings."""
    cols = table.get("columns") or []
    out = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cells = [rfmt(row.get(c)) for c in cols if row.get(c) is not None]
        if cells:
            out.append(" · ".join(cells))
    return out


def _file_labels(models: list[dict]) -> list[str]:
    """Unique display names for the per-file branches.

    The client renders `Files` as an object keyed by name, and nothing stops two
    uploads sharing an original filename — two vendors both send `stock.xls`.
    Keying on the bare name silently dropped one of them, so the per-file
    branches stopped reconciling with the headline totals with no visible sign
    that anything was missing. Repeats are numbered from 2; a unique name is
    left exactly as it was.
    """
    seen: dict[str, int] = {}
    labels: list[str] = []
    for m in models:
        name = str(m.get("filename") or "").strip()
        if not name:
            labels.append("")
            continue
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return labels


def _finding_note(raw: Any) -> dict[str, str]:
    """One ``findings_detail`` string as a drawable note.

    The producer writes ``f"{check}={status} {detail}"`` (``quality.py:215``), so
    all three fields come out of one string and have to be taken apart once, in
    one place: splitting it twice let ``Detail`` land on the status token, so
    ``empty_or_null=fail`` reached the reader as a failure described as "fail".

    A status that does not parse becomes ``info`` rather than ``critical``.
    Overstating a finding as a failure is the expensive direction to be wrong in.
    """
    text = str(raw)
    subject, sep, rest = text.partition("=")
    if not sep:
        return {"Severity": "info", "Subject": text.strip() or "Quality check", "Detail": ""}

    status, _, detail = rest.strip().partition(" ")
    return {
        "Severity": {"fail": "critical", "error": "critical", "warn": "warn"}.get(
            status.strip().lower(), "info"
        ),
        "Subject": subject.strip() or "Quality check",
        "Detail": detail.strip(),
    }


def _file_projection(f: Any, model: dict) -> dict:
    """Per-file branch: everything a reviewer needs, nothing they don't."""
    dq = f.get("data_quality") or {}
    checks = model.get("checks") or []
    failed = [c for c in checks if c.get("status") == "fail"]
    warned = [c for c in checks if c.get("status") == "warn"]

    quality: dict[str, Any] = {
        "Score": rfmt(model.get("quality_score")),
        "Verdict": _quality_verdict(model.get("quality_score"), len(failed)),
        "Checks passed": f"{len(checks) - len(failed) - len(warned)} of {len(checks)}",
        "Failed checks": ", ".join(c.get("check") or "" for c in failed) or "None",
        "Warnings": ", ".join(c.get("check") or "" for c in warned) or "None",
        "Duplicate rows": rfmt(model.get("duplicate_count")),
        # `build_file_model` stores the findings themselves and never a separate
        # count, so the count has to come from the list. Reading a non-existent
        # `findings_count` printed "None" here while the headline reported a
        # non-zero number — a reviewer drilling into the file that actually had
        # the findings was told there were none.
        "Findings": _fmt(len(model.get("findings") or [])),
    }
    if model.get("findings"):
        # `findings_detail` emits one string per non-passing check, shaped
        # `"<check>=<status> <detail>"` (quality.py:215). The status was being
        # thrown away here, so a failed check and a warning arrived at the client
        # as the same grey line of text. Send a tagged notes list instead: the
        # client already renders `$notes` with severity markers, and this is the
        # one place in the branch where the reader most needs to tell a failure
        # from an advisory.
        quality["Finding detail"] = {"$notes": [_finding_note(r) for r in model["findings"]]}

    domain: dict[str, Any] = {}
    dm = model.get("domain") or {}
    for kpi in dm.get("kpis") or []:
        domain[str(kpi.get("label"))] = rfmt(kpi.get("value"))

    # A gross margin can be a currency total or a unit-economics figure
    # depending on which columns the engine found; state which, or the two
    # figures read as a contradiction.
    raw_domain = f.get("domain_analytics") or {}
    if isinstance(raw_domain, dict) and raw_domain.get("margin_basis"):
        domain["Margin basis"] = rfmt(raw_domain["margin_basis"])
    for bar_group in dm.get("bars") or []:
        title = str(bar_group.get("title") or "Breakdown")
        # Two or more bars go to the client as a chart; a single bar stays a
        # flat value, because a one-row bar chart draws nothing a number does not.
        node = _bar_chart(bar_group)
        if node is not None:
            domain[title] = node
            continue
        lines = _bar_lines(bar_group)
        if lines:
            domain[title] = lines
    for table in dm.get("tables") or []:
        title = str(table.get("title") or "Table")
        node = _table_chart(table)
        if node is not None:
            domain[title] = node
            continue
        lines = _table_lines(table)
        if lines:
            domain[title] = lines
    if dm.get("skipped"):
        domain["Not analyzed"] = " · ".join(str(s) for s in dm["skipped"])

    insights: dict[str, Any] = {}
    im = model.get("insights") or {}
    for group in im.get("groups") or []:
        items = group.get("items") or []
        if not items:
            continue
        insights[str(group.get("title") or "Insights")] = {
            f"{i.get('label')}": rfmt(i.get("value")) for i in items if i.get("label")
        }
    # Skipped insight rules are limitations on the report, not results, so they
    # get their own tagged list rather than being buried as a sibling of the
    # insights that did compute.
    insight_notes: list[dict[str, Any]] = []
    for note in im.get("notes") or []:
        missing = note.get("missing") or ""
        insight_notes.append({
            "Severity": "warn",
            "Subject": f"Skipped — {note.get('label')}",
            "Detail": (
                f"{note.get('detail') or note.get('status')}"
                + (f" (needs: {missing})" if missing else "")
            ),
        })
    if insight_notes:
        insights["Not computed"] = {"$notes": insight_notes}

    columns: dict[str, Any] = {}
    profile_rows = (model.get("profile_rows") or [])[:40]
    for row in profile_rows:
        name = row.get("name")
        if not name:
            continue
        entry = {
            "Type": row.get("dtype") or row.get("kind") or "unknown",
            "Nulls": row.get("null_pct"),
            "Unique": row.get("unique_pct"),
        }
        if row.get("stats"):
            entry["Statistics"] = row["stats"]
        if row.get("top_values"):
            entry["Most frequent"] = row["top_values"]
        columns[str(name)] = entry

    # The per-column profile is a grid, not a tree: one row per column, and the
    # numbers that matter to a reviewer are the null and unique rates. Sent as a
    # flat `{name: {Type, Nulls, Unique}}` map it arrived in the client as a nest
    # thirteen keys deep that had to be clicked through to read. The dashboard
    # has been charting `null_pct` from this same source all along
    # (`dashboard/app.js:851-856`); here it becomes a table instead, because the
    # reader is comparing rates across columns rather than ranking one measure.
    #
    # It is built from `columns` rather than from `profile_rows` so the table and
    # the detail map below cannot disagree about a column's type or rates -- the
    # two views of one measurement have to be the same measurement.
    profile_table = _table_chart({
        "headers": ["Column", *PROFILE_TABLE_KEYS],
        "rows": [
            [name, *(entry.get(key) or "" for key in PROFILE_TABLE_KEYS)]
            for name, entry in columns.items()
        ],
    })

    branch: dict[str, Any] = {
        "Type": rfmt(model.get("file_type")),
        "Rows": rfmt(model.get("row_count")),
        "Columns": rfmt(model.get("column_count")),
        "Size": f"{_fmt(model.get('size_bytes') or 0)} bytes",
        "Detected category": rfmt(model.get("top_category")),
        "Quality": quality,
    }

    # The category bars are already `{label, pct}` pairs -- structured chart data
    # that was being flattened to text purely because the client had no way to
    # draw it. Keep them structured now that it does.
    confidence = model.get("category_bars") or []
    if len(confidence) >= 2:
        branch["Category confidence"] = {
            "$chart": "bar",
            "Bars": [
                {
                    "Label": str(b.get("label")),
                    "Value": f"{_num(b.get('pct')) and b['pct'] or 0:.1f}%",
                    "Share": f"{_num(b.get('pct')) and b['pct'] or 0:.1f}%",
                }
                for b in confidence[:CLIENT_BAR_MAX]
            ],
        }
    elif confidence:
        branch["Category confidence"] = [
            f"{b['label']} — {b['pct']}%" for b in confidence
        ]

    forecast_node = _forecast_node(model.get("forecast"))
    if forecast_node is not None:
        branch["Forecast"] = forecast_node

    # A file with no sales date column produces no forecast, and the absence is
    # the finding -- silently omitting it reads as "nothing to say" rather than
    # "we could not project this file".

    # Say plainly when "Rows" is not every row the file contained. Structural
    # report rows are excluded from the money aggregates, so without this the
    # row count reads as full coverage when it is a filtered view.
    row_filter = model.get("row_filter") or {}
    if row_filter.get("dropped_rows"):
        branch["Rows analysed"] = (
            f"{_fmt(row_filter.get('kept_rows') or 0)} of "
            f"{_fmt(row_filter.get('source_rows') or 0)} source rows"
        )
        branch["Rows excluded"] = (
            f"{_fmt(row_filter['dropped_rows'])} structural row(s) — "
            "no product identity and no money value; excluded from money metrics"
        )
    if model.get("multi_sheet"):
        branch["Sheets"] = [
            f"{r.get('sheet')} — {_fmt(r.get('rows') or 0)} rows"
            + (f" ({r.get('top_category')})" if r.get("top_category") else "")
            for r in (model.get("rows") or [])
        ]
    if domain:
        branch["Business metrics"] = domain
    if insights:
        branch["Insights"] = insights
    # The profile table replaces the map, not augments it: shipping the rates in
    # both places put the same null and unique percentages on the page twice
    # under two headings, which reads as two disagreeing measurements. So the
    # detail map keeps only what the table cannot show -- statistics and
    # most-frequent values -- and a column with neither is simply absent.
    detail: dict[str, dict[str, Any]] = {}
    for name, entry in columns.items():
        extra = {k: v for k, v in entry.items() if k not in PROFILE_TABLE_KEYS}
        if extra:
            detail[name] = extra
    if profile_table is not None:
        branch["Column profile"] = profile_table
    if detail:
        branch["Column detail"] = detail
    # A file that could not be fully read, or that had structural rows stripped,
    # are the two things most likely to make a reviewer over-trust a number.
    # Both go in as notes so they read as caveats rather than as data.
    problem_notes: list[dict[str, Any]] = []
    for err in model.get("errors") or []:
        problem_notes.append({"Severity": "critical", "Subject": "Read error", "Detail": str(err)})
    # Ingestion notes record how the sheet was located and shaped (header row,
    # repeated blocks, discarded spacers). They are not warnings, but a reviewer
    # reading only the numbers would otherwise have no way to know the table was
    # reconstructed rather than read straight off the sheet.
    for note in model.get("notes") or []:
        problem_notes.append({
            "Severity": "info",
            "Subject": "How this table was read",
            "Detail": str(note),
        })
    if row_filter.get("dropped_rows"):
        problem_notes.append({
            "Severity": "info",
            "Subject": "Structural rows",
            "Detail": (
                f"{_fmt(row_filter['dropped_rows'])} of "
                f"{_fmt(row_filter.get('source_rows') or 0)} rows had no product "
                "identity and no money value, so they are excluded from every "
                "money metric in this file"
            ),
        })
    if problem_notes:
        branch["Caveats"] = {"$notes": problem_notes}
    return branch


def build_client_projection(
    report: Any, summary: Any = None, analysis_id: Any = None
) -> dict:
    """Project a persisted analysis into SiroQ-Client's ``ReportResultData``.

    The client renders a recursive label→value tree
    (``ReportResultData = { [key: string]: ReportNode }``), so this returns
    decision-grade metrics as flat, pre-formatted strings at the top level and
    keeps drill-down detail in nested branches that the same renderer recurses
    into.

    Derived from :func:`build_file_model` / :func:`_interpret_domain` so the
    client JSON, the printable report and the dashboard can never disagree about
    what the evidence says.

    ``analysis_id`` is passed in rather than read from ``report`` because the
    persisted report document does not carry its own analysis id -- the consumer
    needs it to cite the exact run behind a decision.
    """
    report = report or {}
    summary = summary or {}

    models = [build_file_model(f) for f in (report.get("files") or []) if isinstance(f, dict)]

    rows_total = 0
    for m in models:
        rows_total += int(m.get("row_count") or 0)

    quality_scores = [
        float(m["quality_score"]) for m in models if _num(m.get("quality_score"))
    ]
    mean_quality = sum(quality_scores) / len(quality_scores) if quality_scores else None
    if mean_quality is None:
        mean_quality = summary.get("data_quality_score")

    total_findings = sum(
        len(m.get("findings") or []) for m in models
    ) or summary.get("findings_count") or 0

    total_failed = sum(
        1 for m in models for c in (m.get("checks") or []) if c.get("status") == "fail"
    )
    total_duplicates = sum(
        int(m.get("duplicate_count") or 0) for m in models if _num(m.get("duplicate_count"))
    )

    categories = summary.get("categories_detected") or {}
    cat_lines: list[str] = []
    for cat, names in categories.items():
        cat_lines.append(f"{cat} — {len(names)} file(s)")
    for m in models:
        if m.get("top_category") and not categories:
            cat_lines.append(f"{m['top_category']} — {m.get('filename')}")

    labels = _file_labels(models)
    failed_files_labels = [
        label
        for m, label in zip(models, labels)
        if any(c.get("status") == "fail" for c in (m.get("checks") or []))
    ]

    projection: dict[str, Any] = {
        "Schema version": CLIENT_SCHEMA_VERSION,
        "Application": rfmt(report.get("application_name")),
        "Analysis ID": rfmt(analysis_id or report.get("analysis_id")),
        "Application ID": rfmt(report.get("application_id")),
        "Analyzed at": rfmt(report.get("analyzed_at")),
        "Engine version": rfmt(report.get("engine_version")),
        "Files analyzed": rfmt(
            len(models) if models else (summary.get("file_count") or 0)
        ),
        "Records examined": _fmt(rows_total or summary.get("total_rows") or 0),
        "Data quality score": f"{mean_quality:.1f}%" if _num(mean_quality) else "Not scored",
        "Quality verdict": _quality_verdict(mean_quality, total_failed),
        "Findings requiring review": _fmt(total_findings),
        "Files failing a quality check": ", ".join(str(f) for f in failed_files_labels) or "None",
        "Duplicate rows": _fmt(total_duplicates),
        "Categories detected": cat_lines or ["None detected"],
        "Highest signal": _agg_severity(models).title(),
    }

    pairs = list(
        zip([f for f in (report.get("files") or []) if isinstance(f, dict)], models)
    )

    if models:
        projection["Files"] = {
            label: _file_projection(f, m)
            for (f, m), label in zip(pairs, labels)
            if label
        }

    notes: list[dict[str, Any]] = []
    for (f, m), label in zip(pairs, labels):
        skipped = ((m.get("domain") or {}).get("skipped")) or []
        if skipped:
            notes.append({
                "Severity": "warn",
                "Subject": label,
                "Detail": f"Not analyzed: {'; '.join(str(s) for s in skipped)}",
            })
        row_filter = m.get("row_filter") or {}
        if row_filter.get("dropped_rows"):
            notes.append({
                "Severity": "info",
                "Subject": label,
                "Detail": (
                    f"{_fmt(row_filter['dropped_rows'])} of "
                    f"{_fmt(row_filter.get('source_rows') or 0)} source rows are "
                    "structural (blank spacer or repeated invoice header) and are "
                    "excluded from the money metrics above"
                ),
            })
        if row_filter.get("reason") and not row_filter.get("dropped_rows"):
            notes.append({
                "Severity": "warn",
                "Subject": label,
                "Detail": str(row_filter["reason"]),
            })
    if notes:
        projection["Evidence gaps"] = {"$notes": notes}

    return projection


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