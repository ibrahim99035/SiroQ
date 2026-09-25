"""Derived, calculated insights over an analyzed dataframe.

The engines in :mod:`app.analytics_service.engines` report raw aggregates
(revenue, stock value, expiry buckets). This module derives the numbers a reader
actually acts on: margins, concentration, dead stock, expiry exposure and
period-over-period trends.

Design rules
------------
1. Every rule is fault tolerant. A rule whose input columns are absent returns
   ``status="skipped"`` with a detail sentence naming the missing column(s)
   rather than raising, so a file missing an optional field loses exactly one
   insight instead of the whole analysis.
2. Every rule declares what it needs, and every result carries a ``unit`` and a
   ``severity`` so the UI can rank and colour results without knowing the
   metric.
3. Rules are pure functions of ``(df, fmap)`` and are individually testable.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service.registry import insight_rule, insight_rules

# Column groups. Each entry is (canonical_field, *literal_column_fallbacks); the
# canonical name is looked up in the classification field map first, then the
# literals are matched against the dataframe's own headers.
REVENUE = ("total_amount", "total_revenue", "revenue", "net_revenue",
           "total", "amount", "sales_amount", "sales")
COST_TOTAL = ("total_cost", "cogs", "total_purchase", "purchase_total", "cost_total")
COST_UNIT = ("unit_cost", "cost_per_unit", "purchase_price", "cost_price")
PRICE = ("unit_price", "selling_price", "price", "sale_price")
NET_PROFIT = ("net_profit", "profit", "net_income", "margin_amount")
QTY_SOLD = ("quantity", "quantity_sold", "qty_sold", "sold_quantity", "qty_sold_units")
STOCK = ("stock_on_hand", "stock_balance", "quantity_on_hand", "on_hand",
         "balance", "stock", "current_stock")
EXPIRY = ("expiry_date", "expiration_date", "exp_date", "expiry", "exp")
BATCH = ("batch_id", "batch_no", "lot_no", "batch_number", "batch", "lot")
PRODUCT = ("product_name", "product", "item_name", "drug_name", "item", "sku_name")
SALE_DATE = ("sale_timestamp", "sale_date", "transaction_date", "invoice_date",
             "date", "datetime", "timestamp")
WASTE_QTY = ("waste_quantity", "waste_qty", "writeoff_quantity", "writeoff_qty",
             "damaged_quantity", "damaged_qty", "expired_quantity", "waste")
EVENT_TYPE = ("event_type", "transaction_type", "movement", "movement_type",
              "stock_movement", "event")
RECEIPT_DATE = ("receipt_date", "received_date", "received")

LABELS = {
    "gross_margin_pct": "Gross margin",
    "net_profit_total": "Net profit",
    "loss_making_products": "Loss-making products",
    "pareto_80": "Products driving 80% of value",
    "revenue_concentration": "Revenue concentration (HHI)",
    "dead_stock_count": "Dead stock (no sales)",
    "overstocked_products": "Overstocked products",
    "tied_up_capital": "Capital tied up in stock",
    "stock_turnover": "Stock turnover",
    "expiry_at_risk": "Expiry value at risk",
    "waste_rate": "Write-off / waste rate",
    "revenue_trend": "Value trend",
    "volume_trend": "Volume trend",
}

SEVERITY_GOOD = "good"
SEVERITY_WARN = "warn"
SEVERITY_BAD = "bad"
SEVERITY_INFO = "info"
SEVERITY_MUTED = "muted"


class InsightContext:
    """Column resolution + numeric coercion shared by every rule."""

    def __init__(self, df: pd.DataFrame, fmap: dict | None):
        self.df = df if df is not None else pd.DataFrame()
        self.fmap = fmap or {}

    def col(self, canonical: str, *fallbacks: str) -> str | None:
        """Resolve one logical column.

        Order: the classification field map, then the canonical name as a plain
        header, then the literal fallbacks. The canonical name is tried as a
        literal too so a file whose columns are already named after the
        canonical fields resolves even when classification was skipped.
        """
        mapped = self.fmap.get(canonical)
        if mapped and mapped in self.df.columns:
            return mapped
        if canonical in self.df.columns:
            return canonical
        for name in fallbacks:
            if name in self.df.columns:
                return name
        return None

    def first(self, *specs: tuple) -> str | None:
        """First column that resolves out of several alternative groups."""
        for spec in specs:
            canonical, *fallbacks = spec
            found = self.col(canonical, *fallbacks)
            if found:
                return found
        return None

    def require(self, *specs: tuple) -> tuple[list[str], list[str]]:
        """Resolve several column groups.

        Returns ``(columns, missing_canonical_names)``. ``columns`` keeps the
        resolved names in the order the specs were given.
        """
        cols: list[str] = []
        missing: list[str] = []
        for spec in specs:
            canonical, *fallbacks = spec
            found = self.col(canonical, *fallbacks)
            if found is None:
                missing.append(canonical)
            else:
                cols.append(found)
        return cols, missing

    def num(self, col: str | None) -> pd.Series:
        if not col or col not in self.df.columns:
            return pd.Series(dtype="float64")
        return pd.to_numeric(self.df[col], errors="coerce")

    def dates(self, col: str | None) -> pd.Series:
        if not col or col not in self.df.columns:
            return pd.Series(dtype="datetime64[ns]")
        return pd.to_datetime(self.df[col], errors="coerce", format="mixed")

    def label_of(self, col: str | None) -> pd.Series:
        if not col or col not in self.df.columns:
            return pd.Series(dtype="object")
        return self.df[col].astype(str)

    def unit_cost(self) -> tuple[pd.Series, str]:
        """Per-unit cost series plus a human description of where it came from.

        A row-level ``total_cost`` is not a unit cost, so valuing stock with it
        would overstate capital by a factor of the quantity. When only a total is
        available it is divided by the quantity sold to get an implied unit
        cost, and the basis is reported so the number is never mistaken for a
        real unit price.
        """
        col = self.first(COST_UNIT)
        if col:
            return self.num(col), col
        total_col = self.first(COST_TOTAL)
        qty_col = self.first(QTY_SOLD)
        if total_col and qty_col:
            qty = self.num(qty_col)
            derived = self.num(total_col) / qty.replace(0, np.nan)
            if derived.notna().any():
                return derived, f"implied ({total_col} / {qty_col})"
        return pd.Series(dtype="float64"), ""


def _ok(value: Any, unit: str, detail: str, *, severity: str = SEVERITY_INFO,
        evidence: dict | None = None) -> dict:
    return {
        "value": value,
        "unit": unit,
        "status": "ok",
        "severity": severity,
        "detail": detail,
        "evidence": evidence or {},
    }


def _skip(missing: list[str], detail: str) -> dict:
    return {
        "value": None,
        "unit": None,
        "status": "skipped",
        "severity": SEVERITY_MUTED,
        "detail": detail,
        "evidence": {},
        "missing_columns": missing,
    }


def _pct(numerator: float, denominator: float) -> float | None:
    if not denominator or not np.isfinite(denominator) or denominator == 0:
        return None
    return round(100.0 * numerator / denominator, 2)


def _safe_sum(s: pd.Series) -> float:
    return float(pd.to_numeric(s, errors="coerce").sum())


def _clean(obj: Any) -> Any:
    """Make numpy scalars and NaN JSON-safe for the report document."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if not np.isfinite(f) else round(f, 4)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (date, pd.Timestamp)):
        return obj.isoformat()
    return obj


# --- profitability ----------------------------------------------------------


def _revenue_and_cost(ctx: InsightContext) -> tuple[pd.Series, pd.Series, str]:
    """Return comparable (revenue, cost) series plus the basis used.

    Row-level totals are preferred because they are unambiguous. When only unit
    economics exist, quantity is used to lift them to totals. Mixing the two
    (a per-unit cost multiplied by a row total, or a row total subtracted from a
    per-unit price) is what previously produced nonsense margins, so each basis
    is only ever compared with its own kind.
    """
    cols, missing = ctx.require(REVENUE, COST_TOTAL)
    if not missing:
        return ctx.num(cols[0]), ctx.num(cols[1]), "row totals"

    cols, missing = ctx.require(QTY_SOLD, PRICE, COST_UNIT)
    if not missing:
        qty, price, unit_cost = (ctx.num(c) for c in cols)
        return qty * price, qty * unit_cost, "unit economics"
    return pd.Series(dtype="float64"), pd.Series(dtype="float64"), ""


@insight_rule("gross_margin_pct", family="profitability", order=10,
              requires=("total_amount", "total_cost", "unit_cost"))
def gross_margin_pct(ctx: InsightContext) -> dict:
    revenue, cost, basis = _revenue_and_cost(ctx)
    if revenue.empty and cost.empty:
        return _skip(
            ["total_amount", "total_cost"],
            "Needs a revenue column and a cost column (total or per-unit).",
        )
    rev_total, cost_total = _safe_sum(revenue), _safe_sum(cost)
    if rev_total == 0:
        return _skip(["total_amount"], "Total revenue is zero; margin undefined.")
    margin = rev_total - cost_total
    pct = _pct(margin, rev_total)
    severity = SEVERITY_BAD if margin < 0 else (SEVERITY_WARN if pct < 10 else SEVERITY_GOOD)
    return _ok(
        pct, "percent",
        f"Margin {pct}% on revenue of {rev_total:,.2f} (basis: {basis}).",
        severity=severity,
        evidence={"revenue": round(rev_total, 2), "cost": round(cost_total, 2),
                  "margin": round(margin, 2), "basis": basis},
    )


@insight_rule("net_profit_total", family="profitability", order=20,
              requires=("net_profit",))
def net_profit_total(ctx: InsightContext) -> dict:
    col = ctx.first(NET_PROFIT)
    if col is None:
        return _skip(["net_profit"], "Needs a net_profit column.")
    total = _safe_sum(ctx.num(col))
    severity = SEVERITY_BAD if total < 0 else SEVERITY_GOOD
    return _ok(
        round(total, 2), "currency",
        f"Net profit {total:,.2f} across {len(ctx.df):,} rows.",
        severity=severity,
        evidence={"rows": int(len(ctx.df)), "total": round(total, 2)},
    )


@insight_rule("loss_making_products", family="profitability", order=30,
              requires=("net_profit", "product_name"))
def loss_making_products(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(NET_PROFIT)
    if missing:
        revenue, cost, _ = _revenue_and_cost(ctx)
        if revenue.empty or cost.empty:
            return _skip(
                ["net_profit"],
                "Needs a net_profit column, or both revenue and cost columns.",
            )
        per_row = revenue - cost
        basis = "revenue minus cost"
    else:
        per_row = ctx.num(cols[0])
        basis = "net_profit"

    prod_col = ctx.first(PRODUCT)
    names = ctx.label_of(prod_col)
    total = _safe_sum(per_row)
    losers = per_row < 0
    count = int(losers.sum())
    if count == 0:
        return _ok(
            0, "products", "No loss-making rows found.",
            severity=SEVERITY_GOOD,
            evidence={"checked": int(per_row.notna().sum()), "basis": basis},
        )
    lost = _safe_sum(per_row[losers])
    share = _pct(count, int(per_row.notna().sum()))
    top = (
        [{"product": str(names.iloc[i]), "value": round(float(per_row.iloc[i]), 2)}
         for i in per_row[losers].sort_values().head(10).index]
        if prod_col else []
    )
    return _ok(
        count, "products",
        f"{count} row(s) lose money ({share}% of rows), totalling {lost:,.2f} "
        f"({basis}); overall {total:,.2f}.",
        severity=SEVERITY_BAD if share and share > 10 else SEVERITY_WARN,
        evidence={"rows": share, "loss_value": round(lost, 2), "basis": basis,
                  "top": top},
    )


# --- concentration ----------------------------------------------------------


@insight_rule("pareto_80", family="concentration", order=40,
              requires=("total_amount", "product_name"))
def pareto_80(ctx: InsightContext) -> dict:
    rev_col = ctx.first(REVENUE)
    if rev_col is None:
        return _skip(["total_amount"], "Needs a revenue column to rank products by.")
    values = ctx.num(rev_col)
    if not values.notna().any() or _safe_sum(values) <= 0:
        return _skip(["total_amount"], "Revenue column has no usable positive totals.")

    prod_col = ctx.first(PRODUCT)
    names = ctx.label_of(prod_col)
    grouped = (
        values.groupby(names).sum().sort_values(ascending=False)
        if names.notna().any() else values.dropna().sort_values(ascending=False)
    )
    total = float(grouped.sum())
    if total <= 0 or grouped.empty:
        return _skip(["total_amount"], "No positive revenue to concentrate.")
    cum = grouped.cumsum() / total
    needed = int((cum < 0.80).sum()) + 1
    needed = min(needed, len(grouped))
    n = len(grouped)
    share_80 = round(100.0 * float(cum.iloc[needed - 1]), 2)
    share_20 = round(100.0 * float(grouped.head(max(1, n // 5)).sum() / total), 2)
    severity = SEVERITY_WARN if n > 1 and needed / n <= 0.2 else SEVERITY_INFO
    return _ok(
        needed, "products",
        f"{needed} of {n:,} value-bearing rows ({_pct(needed, n)}%) produce 80% "
        f"of value; the top 20% produce {share_20}%.",
        severity=severity,
        evidence={"total_rows": n, "share_80": share_80, "top20_share": share_20,
                  "top": [{"label": str(k), "value": round(float(v), 2)}
                          for k, v in grouped.head(10).items()]},
    )


@insight_rule("revenue_concentration", family="concentration", order=50,
              requires=("total_amount",))
def revenue_concentration(ctx: InsightContext) -> dict:
    rev_col = ctx.first(REVENUE)
    if rev_col is None:
        return _skip(["total_amount"], "Needs a revenue column.")
    values = ctx.num(rev_col).dropna()
    values = values[values > 0]
    total = _safe_sum(values)
    if total <= 0 or values.empty:
        return _skip(["total_amount"], "No positive revenue to measure concentration.")
    shares = values / total
    hhi = float((shares ** 2).sum()) * 10000
    if hhi >= 2500:
        verdict, severity = "highly concentrated", SEVERITY_WARN
    elif hhi >= 1500:
        verdict, severity = "moderately concentrated", SEVERITY_INFO
    else:
        verdict, severity = "competitive / spread out", SEVERITY_GOOD
    return _ok(
        round(hhi, 1), "index",
        f"HHI {hhi:,.0f} — {verdict} (above 2,500 means a few rows dominate).",
        severity=severity, evidence={"hhi": round(hhi, 1)},
    )


# --- overstock / dead stock -------------------------------------------------


@insight_rule("dead_stock_count", family="waste", order=60,
              requires=("stock_on_hand", "quantity", "product_name"))
def dead_stock_count(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(STOCK, QTY_SOLD)
    if missing:
        return _skip(
            missing,
            "Needs an on-hand stock column and a quantity-sold column.",
        )
    stock, sold = ctx.num(cols[0]), ctx.num(cols[1])
    dead = stock.gt(0) & sold.fillna(0).le(0)
    count = int(dead.sum())
    stranded = _safe_sum(stock[dead])
    total_stock = _safe_sum(stock)
    if count == 0:
        return _ok(0, "products", "No dead stock: every stocked row shows sales.",
                   severity=SEVERITY_GOOD)
    return _ok(
        count, "products",
        f"{count} stocked row(s) with zero sales, holding {stranded:,.0f} units "
        f"({_pct(stranded, total_stock) or 0}% of stock).",
        severity=SEVERITY_BAD if (total_stock and stranded / total_stock > 0.1) else SEVERITY_WARN,
        evidence={"units": round(stranded, 2), "share_of_stock": _pct(stranded, total_stock)},
    )


@insight_rule("overstocked_products", family="waste", order=70,
              requires=("stock_on_hand", "quantity"))
def overstocked_products(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(STOCK, QTY_SOLD)
    if missing:
        return _skip(
            missing, "Needs an on-hand stock column and a quantity-sold column."
        )
    stock, sold = ctx.num(cols[0]), ctx.num(cols[1])
    sold_pos = sold.clip(lower=0)
    ratio = stock / sold_pos.replace(0, np.nan)

    frame = pd.DataFrame({"stock": stock, "sold": sold_pos, "ratio": ratio})
    prod_col = ctx.first(PRODUCT)
    names = ctx.label_of(prod_col)
    frame["label"] = names if names.notna().any() else frame.index.astype(str)
    hot = frame[frame["stock"] > 0].dropna(subset=["ratio"])
    hot = hot[hot["ratio"] >= 3]
    if hot.empty:
        return _ok(0, "products",
                   "No product holds 3x or more stock than it has sold.",
                   severity=SEVERITY_GOOD)
    hot = hot.sort_values("ratio", ascending=False)
    cost, cost_basis = ctx.unit_cost()
    excess_units = float((hot["stock"] - hot["sold"]).clip(lower=0).sum())
    excess_value = None
    if not cost.empty:
        per_row_excess = (stock - sold_pos).clip(lower=0)
        mask = per_row_excess.gt(0)
        excess_value = round(_safe_sum(per_row_excess[mask] * cost[mask]), 2)
    return _ok(
        int(len(hot)), "products",
        f"{len(hot)} product(s) hold 3x+ stock versus units sold "
        f"({excess_units:,.0f} excess units).",
        severity=SEVERITY_WARN,
        evidence={
            "excess_units": round(excess_units, 2),
            "excess_value": excess_value,
            "cost_basis": cost_basis or None,
            "top": [
                {"product": str(r["label"]), "stock": round(float(r["stock"]), 2),
                 "sold": round(float(r["sold"]), 2), "ratio": round(float(r["ratio"]), 1)}
                for _, r in hot.head(10).iterrows()
            ],
        },
    )


@insight_rule("tied_up_capital", family="waste", order=80,
              requires=("stock_on_hand", "unit_cost"))
def tied_up_capital(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(STOCK)
    if missing:
        return _skip(["stock_on_hand"], "Needs an on-hand stock column.")
    stock = ctx.num(cols[0])
    cost, basis = ctx.unit_cost()
    units = _safe_sum(stock)
    if cost.empty:
        return _ok(
            round(units, 2), "units",
            f"{units:,.0f} units on hand. Add a unit_cost (or total_cost plus "
            "quantity) column to value this.",
            severity=SEVERITY_INFO,
        )
    value = _safe_sum((stock * cost).clip(lower=0))
    return _ok(
        round(value, 2), "currency",
        f"{value:,.2f} tied up across {units:,.0f} units on hand "
        f"(unit cost basis: {basis}).",
        severity=SEVERITY_INFO,
        evidence={"units": round(units, 2), "cost_basis": basis},
    )


@insight_rule("stock_turnover", family="waste", order=90,
              requires=("stock_on_hand", "quantity"))
def stock_turnover(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(STOCK, QTY_SOLD)
    if missing:
        return _skip(
            missing, "Needs an on-hand stock column and a quantity-sold column."
        )
    stock, sold = _safe_sum(ctx.num(cols[0])), _safe_sum(ctx.num(cols[1]))
    if stock <= 0:
        return _skip(["stock_on_hand"], "No stock on hand; turnover undefined.")
    turns = sold / stock
    if turns < 0.5:
        severity, verdict = SEVERITY_BAD, "slow — stock is barely moving"
    elif turns < 1.0:
        severity, verdict = SEVERITY_WARN, "below one turn per period"
    else:
        severity, verdict = SEVERITY_GOOD, "healthy"
    return _ok(
        round(turns, 2), "ratio",
        f"{turns:.2f}x turnover ({sold:,.0f} sold against {stock:,.0f} on hand) — {verdict}.",
        severity=severity, evidence={"sold": round(sold, 2), "stock": round(stock, 2)},
    )


# --- expiry / write-off waste ----------------------------------------------


@insight_rule("expiry_at_risk", family="waste", order=100,
              requires=("expiry_date", "stock_on_hand"))
def expiry_at_risk(ctx: InsightContext) -> dict:
    cols, missing = ctx.require(EXPIRY, STOCK)
    if missing:
        return _skip(
            missing, "Needs an expiry_date column and an on-hand stock column."
        )
    exp, stock = ctx.dates(cols[0]), ctx.num(cols[1])
    valid = exp.notna()
    if not valid.any():
        return _skip(["expiry_date"], "Expiry column holds no parseable dates.")
    days = (exp[valid] - pd.Timestamp(date.today())).dt.days
    buckets = {"expired": int((days < 0).sum())}
    for horizon in (30, 60, 90, 180):
        buckets[str(horizon)] = int(((days >= 0) & (days < horizon)).sum())
    cost, _ = ctx.unit_cost()
    at_risk_90 = (days < 90) & (days >= 0)
    value_90 = None
    if not cost.empty:
        value_90 = round(
            _safe_sum(
                (stock[valid][at_risk_90] * cost[valid][at_risk_90]).clip(lower=0)
            ),
            2,
        )
    within_90 = int(at_risk_90.sum())
    already = buckets["expired"]
    severity = SEVERITY_BAD if (already or within_90) else SEVERITY_GOOD
    parts = [f"{within_90} row(s) expire within 90 days"]
    if already:
        parts.append(f"{already} already expired")
    if value_90 is not None:
        parts.append(f"value at risk {value_90:,.2f}")
    return _ok(
        within_90, "rows",
        "; ".join(parts) + "." if within_90 or already else "Nothing expires within 180 days.",
        severity=severity,
        evidence={"buckets": buckets, "value_90d": value_90},
    )


@insight_rule("waste_rate", family="waste", order=110,
              requires=("waste_quantity",))
def waste_rate(ctx: InsightContext) -> dict:
    waste_col = ctx.first(WASTE_QTY)
    event_col = ctx.first(EVENT_TYPE)
    if waste_col is None and event_col is None:
        return _skip(
            ["waste_quantity"],
            "Needs a waste/write-off quantity column, or an event_type column "
            "with waste/write-off/damage/expiry values.",
        )
    mask = pd.Series(False, index=ctx.df.index)
    if waste_col:
        mask = ctx.num(waste_col).fillna(0).gt(0)
    if event_col:
        events = ctx.df[event_col].astype(str).str.lower()
        mask = mask | events.str.contains(
            "waste|writeoff|write-off|damage|damaged|expired|expiry|dispose", na=False
        )
    count = int(mask.sum())
    if count == 0:
        return _ok(0, "rows", "No waste, write-off, damage or expiry events found.",
                   severity=SEVERITY_GOOD)
    qty = ctx.num(waste_col)[mask].sum() if waste_col else np.nan
    cost, _ = ctx.unit_cost()
    value = None
    if not cost.empty:
        value = round(
            _safe_sum((ctx.num(waste_col)[mask] * cost[mask]).clip(lower=0))
            if waste_col else 0.0, 2
        )
    rev_col = ctx.first(REVENUE)
    rate = _pct(value, _safe_sum(ctx.num(rev_col))) if (value is not None and rev_col) else None
    return _ok(
        round(float(qty), 2) if qty == qty else count, "units",
        f"{count} waste event row(s)"
        + (f" totalling {qty:,.0f} units" if qty == qty else "")
        + (f", {value:,.2f} in value" if value is not None else "")
        + (f" ({rate}% of revenue)" if rate is not None else "")
        + ".",
        severity=SEVERITY_BAD if (rate or 0) > 2 else SEVERITY_WARN,
        evidence={"rows": count, "quantity": round(float(qty), 2) if qty == qty else None,
                  "value": value, "rate_pct": rate},
    )


# --- trends -----------------------------------------------------------------


def _period_for(span_days: float) -> tuple[str, str]:
    if span_days <= 31:
        return "D", "day"
    if span_days <= 200:
        return "W-MON", "week"
    return "MS", "month"


def _trend(ctx: InsightContext, value_col: str | None, unit: str) -> dict:
    if value_col is None:
        return _skip(["sale_timestamp"], "Needs a date column to compute a trend.")
    dates = ctx.dates(ctx.first(SALE_DATE))
    if dates is None or dates.empty or not dates.notna().any():
        return _skip(["sale_timestamp"], "Date column holds no parseable dates.")
    values = ctx.num(value_col)
    frame = pd.DataFrame({"d": dates, "v": values}).dropna()
    if frame.empty:
        return _skip(["sale_timestamp"], "No rows with both a date and a value.")
    span = (frame["d"].max() - frame["d"].min()).days
    freq, label = _period_for(span)
    rolled = frame.set_index("d")["v"].resample(freq).sum()
    rolled = rolled[rolled.notna()]
    if len(rolled) < 3:
        return _skip(
            ["sale_timestamp"],
            f"Only {len(rolled)} {label}(s) of history; need at least 3 to trend.",
        )
    last, prev = float(rolled.iloc[-1]), float(rolled.iloc[-2])
    growth = _pct(last - prev, prev)
    first_half = float(rolled.head(len(rolled) // 2).mean())
    second_half = float(rolled.tail(len(rolled) // 2).mean())
    overall = _pct(second_half - first_half, first_half)
    slope = float(rolled.diff().mean())
    if growth is None or abs(growth) < 2:
        direction, severity = "flat", SEVERITY_INFO
    elif growth > 0:
        direction, severity = "up", SEVERITY_GOOD
    else:
        direction, severity = "down", SEVERITY_BAD if growth < -10 else SEVERITY_WARN
    return _ok(
        growth, "percent",
        f"{label.title()}ly {label} total {growth:+}% vs the previous {label} "
        f"({prev:,.0f} -> {last:,.0f}); second half vs first half {overall:+}%. "
        f"Direction: {direction}.",
        severity=severity,
        evidence={
            "granularity": label, "periods": int(len(rolled)),
            "latest": round(last, 2), "previous": round(prev, 2),
            "growth_pct": growth, "overall_pct": overall, "direction": direction,
            "slope_per_period": round(slope, 2),
            "series": [{"period": str(k.date()), "value": round(float(v), 2)}
                       for k, v in rolled.tail(24).items()],
        },
    )


@insight_rule("revenue_trend", family="trend", order=120,
              requires=("sale_timestamp", "total_amount"))
def revenue_trend(ctx: InsightContext) -> dict:
    rev_col = ctx.first(REVENUE)
    date_col = ctx.first(SALE_DATE)
    if date_col is None:
        return _skip(["sale_timestamp"],
                     "Needs a date column (sale_timestamp / sale_date) to trend over time.")
    if rev_col is None:
        return _skip(["total_amount"], "Needs a revenue column to trend.")
    return _trend(ctx, rev_col, "currency")


@insight_rule("volume_trend", family="trend", order=130,
              requires=("sale_timestamp", "quantity"))
def volume_trend(ctx: InsightContext) -> dict:
    date_col = ctx.first(SALE_DATE)
    if date_col is None:
        return _skip(["sale_timestamp"],
                     "Needs a date column (sale_timestamp / sale_date) to trend over time.")
    qty_col = ctx.first(QTY_SOLD)
    return _trend(ctx, qty_col, "count")


# --- dispatcher -------------------------------------------------------------


def _run_rule(entry, ctx: InsightContext) -> dict:
    key = entry.name
    result: dict[str, Any] = {
        "key": key,
        "family": entry.meta.get("family", "general"),
        "label": LABELS.get(key, key.replace("_", " ").capitalize()),
        "value": None,
        "unit": None,
        "status": "skipped",
        "severity": SEVERITY_MUTED,
        "detail": "",
        "evidence": {},
        "requires": list(entry.meta.get("requires", ())),
    }
    try:
        produced = entry.fn(ctx) or {}
    except Exception as exc:
        produced = {
            "value": None, "unit": None, "status": "error",
            "severity": SEVERITY_MUTED,
            "detail": f"Rule failed: {type(exc).__name__}: {exc}",
            "evidence": {},
        }
    if not isinstance(produced, dict):
        produced = {"value": None, "detail": "Rule returned an unusable result."}
    if produced.get("value") is not None and produced.get("status") != "skipped":
        produced.setdefault("status", "ok")
        produced.setdefault("severity", SEVERITY_INFO)
    for field in ("value", "unit", "status", "severity", "detail", "evidence",
                  "missing_columns"):
        if field in produced:
            result[field] = produced[field]
    return _clean(result)


def compute_insights(df: pd.DataFrame, fmap: dict | None,
                     category: str | None = None) -> list[dict]:
    """Run every registered insight rule and return JSON-safe results.

    A rule that raises is reported as an ``error`` entry rather than propagating,
    and a rule whose columns are absent is reported as ``skipped`` with the
    missing column names, so partial data still yields partial insight.
    """
    ctx = InsightContext(df, fmap)
    entries = sorted(insight_rules.all(), key=lambda e: e.meta.get("order", 100.0))
    out = [_run_rule(entry, ctx) for entry in entries]
    if category:
        for item in out:
            item["category"] = category
    return out
