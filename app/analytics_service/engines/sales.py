"""Sales domain analytics engine."""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics_service.engines._helpers import col_sum, numeric, pick_col, safe_sum
from app.analytics_service.registry import domain_engine


@domain_engine("sales")
def sales_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    amount_col = pick_col(df, fmap, "total_amount", "total_amount", "total", "amount", "revenue", "total_revenue")
    ts_col = pick_col(df, fmap, "sale_timestamp", "sale_timestamp", "sale_date", "transaction_date", "datetime")
    tx_col = pick_col(df, fmap, "transaction_ref", "transaction_ref", "transaction_id", "receipt_id", "invoice_no", "sale_id")
    pay_col = pick_col(df, fmap, "payment_method", "payment_method", "payment_type", "payment")
    qty_col = pick_col(df, fmap, "quantity", "quantity", "qty", "qty_sold", "quantity_sold")
    price_col = pick_col(df, fmap, "unit_price", "unit_price", "price", "unit_cost", "selling_price")
    prod_col = pick_col(df, fmap, "product_name", "product_name", "product", "item", "drug")
    cost_col = pick_col(df, fmap, "cost_per_unit", "unit_cost", "product_cost", "cost", "total_cost")

    skipped = []
    if not amount_col:
        skipped.append("total_amount")
    if not ts_col:
        skipped.append("sale_timestamp")

    out: dict[str, Any] = {"category": "sales", "skipped": skipped}
    # the report labels the top-products chart with this, so it must be recorded
    out["amount_field"] = amount_col
    if amount_col:
        revenue = col_sum(df, amount_col)
        out["revenue"] = round(revenue, 2)
        if tx_col and revenue:
            out["transactions"] = int(df[tx_col].nunique())
            out["avg_basket"] = round(revenue / max(out["transactions"], 1), 2)
        else:
            out["transactions"] = int(len(df))
            out["avg_basket"] = round(revenue / max(len(df), 1), 2)
        if pay_col:
            paid = (
                df.assign(_g=df[pay_col].astype(str))
                .groupby("_g")[amount_col]
                .apply(safe_sum)
            )
            out["payment_mix"] = [{"method": k, "amount": round(float(v), 2)}
                                  for k, v in paid.head(20).items()]
        if prod_col and revenue:
            by_prod = (
                df.assign(_g=df[prod_col].astype(str))
                .groupby("_g")[amount_col]
                .apply(safe_sum)
            )
            out["top_products"] = [{"product": k, "amount": round(float(v), 2)}
                                   for k, v in by_prod.nlargest(10).items()]
    if ts_col:
        ts = pd.to_datetime(df[ts_col], errors="coerce")
        if ts.notna().any():
            frame = pd.DataFrame({"_d": ts, "_n": pd.Series(range(len(df)), index=df.index)})
            frame = frame.dropna(subset=["_d"])
            grouped = frame.groupby(frame["_d"].dt.date)
            if amount_col:
                # Value-aware series: a trend needs the summed amount per day,
                # not just how many rows landed on it. ``count`` is kept so
                # existing consumers (dashboard/report) keep working.
                amounts = numeric(df, amount_col)
                value_by_day = amounts.groupby(frame["_d"].dt.date).sum()
                out["daily_series"] = [
                    {"date": str(d), "count": int(c),
                     "value": round(float(value_by_day.get(d, 0.0)), 2)}
                    for d, c in grouped["_n"].count().items()
                ]
            else:
                out["daily_series"] = [
                    {"date": str(d), "count": int(c), "value": int(c)}
                    for d, c in grouped["_n"].count().items()
                ]
        else:
            skipped.append("sale_timestamp(parse)")
    if qty_col and price_col:
        out["gross_merchandise_value"] = round(
            float((numeric(df, qty_col) * numeric(df, price_col)).sum()), 2)
    # Margin is only meaningful when both sides are the same kind of quantity.
    # A per-unit cost must never be multiplied by a row total, and a row total
    # must never be subtracted from a per-unit price -- mixing the two produced
    # multi-million-unit nonsense margins on per-product files.
    total_rev_col = pick_col(df, fmap, "total_amount", "total_revenue", "revenue", "net_revenue")
    total_cost_col = pick_col(df, fmap, "total_cost", "total_cost", "cogs", "total_purchase")
    if total_rev_col and total_cost_col:
        margin = float(numeric(df, total_rev_col).sum() - numeric(df, total_cost_col).sum())
        rev_total = float(numeric(df, total_rev_col).sum())
        out["margin_basis"] = "row totals"
    elif qty_col and price_col and cost_col:
        revenue_lines = numeric(df, qty_col) * numeric(df, price_col)
        cost_lines = numeric(df, qty_col) * numeric(df, cost_col)
        margin = float((revenue_lines - cost_lines).sum())
        rev_total = float(revenue_lines.sum())
        out["margin_basis"] = "unit economics"
    else:
        margin = None
    if margin is not None:
        out["gross_margin"] = round(margin, 2)
        if rev_total:
            out["gross_margin_pct"] = round(100.0 * margin / rev_total, 2)
    return out
