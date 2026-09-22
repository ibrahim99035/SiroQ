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
        ts = pd.to_datetime(df[ts_col], errors="coerce").dropna()
        if len(ts):
            days = ts.dt.date.value_counts().sort_index()
            out["daily_series"] = [{"date": str(d), "count": int(c)}
                                   for d, c in days.items()]
        else:
            skipped.append("sale_timestamp(parse)")
    if qty_col and price_col:
        out["gross_merchandise_value"] = round(
            float((numeric(df, qty_col) * numeric(df, price_col)).sum()), 2)
    if qty_col and price_col and cost_col:
        revenue_lines = numeric(df, qty_col) * numeric(df, price_col)
        cost_lines = numeric(df, qty_col) * numeric(df, cost_col)
        margin = float((revenue_lines - cost_lines).sum())
        out["gross_margin"] = round(margin, 2)
    return out