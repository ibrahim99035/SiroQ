"""Purchase-orders domain analytics engine (fill rate, PO value, cost variance)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.analytics_service.engines._helpers import categorical_counts, numeric, pick_col
from app.analytics_service.registry import domain_engine


@domain_engine("purchase_orders")
def purchase_order_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    po_col = pick_col(df, fmap, "po_number", "purchase_order_id", "po", "order_no")
    ordered = pick_col(df, fmap, "ordered_quantity", "order_quantity")
    received = pick_col(df, fmap, "received_quantity")
    cost_col = pick_col(df, fmap, "unit_cost")
    std_col = pick_col(df, fmap, "standard_cost", "std_cost")
    out: dict[str, Any] = {"category": "purchase_orders", "skipped": []}
    if po_col:
        out["po_count"] = int(df[po_col].nunique())
    if ordered:
        o = numeric(df, ordered)
        out["total_ordered_quantity"] = round(float(o.sum()), 3)
        if received:
            r = numeric(df, received)
            rate = (r / o.replace(0, np.nan)).dropna()
            out["fill_rate"] = {
                "mean": round(float(rate.mean() * 100), 1),
                "below_90_count": int((rate * 100 < 90).sum()),
            }
    if cost_col and ordered:
        out["total_po_value"] = round(float((numeric(df, ordered) * numeric(df, cost_col)).sum()), 2)
    if cost_col and std_col:
        variance = numeric(df, cost_col) - numeric(df, std_col)
        out["cost_variance_total"] = round(float(variance.fillna(0).sum()), 2)
    out.update(categorical_counts(df, ["status", "delivery_status", "supplier"]))
    return out