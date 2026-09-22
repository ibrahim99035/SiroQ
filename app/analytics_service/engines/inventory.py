"""Inventory domain analytics engine (stock value, ABC/XYZ, expiry risk)."""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.analytics_service.engines._helpers import numeric, pick_col
from app.analytics_service.registry import domain_engine


@domain_engine("inventory")
def inventory_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    batch_col = pick_col(df, fmap, "batch_id", "batch_id", "batch", "lot", "lot_number")
    qty_col = pick_col(df, fmap, "quantity", "quantity", "qty", "quantity_on_hand", "on_hand")
    expiry_col = pick_col(df, fmap, "expiry_date", "expiry_date", "expiration_date", "exp")
    cost_col = pick_col(df, fmap, "unit_cost", "cost_per_unit", "unit_price", "price")
    event_col = pick_col(df, fmap, "event_type", "transaction_type")
    waste_cols = [c for c in df.columns
                  if any(t in c for t in ("waste", "writeoff", "damage", "damaged"))]

    skipped = []
    if not qty_col:
        skipped.append("quantity")
    if not expiry_col:
        skipped.append("expiry_date")

    out: dict[str, Any] = {"category": "inventory", "skipped": skipped}
    if qty_col:
        qty = numeric(df, qty_col)
        out["quantity_on_hand"] = round(float(qty.sum()), 3)
        if cost_col:
            value = float((qty * numeric(df, cost_col)).sum())
            out["stock_value"] = round(value, 2)
            # ABC/XYZ: cumulative-value share across lots -> A <=80%, B<=95%, C rest.
            if batch_col:
                lot_value = (qty * numeric(df, cost_col)).groupby(
                    df[batch_col].astype(str)).sum().sort_values(ascending=False)
                total = max(float(lot_value.sum()), 1.0)
                cum = 0.0
                classes = {}
                for lot, v in lot_value.items():
                    cum += float(v)
                    share = cum / total
                    classes[lot] = "A" if share <= 0.8 else ("B" if share <= 0.95 else "C")
                out["abc_classes"] = classes
    if expiry_col:
        exp = pd.to_datetime(df[expiry_col], errors="coerce").dropna()
        if len(exp):
            today = date.today()
            days_to = pd.Series([(d.date() - today).days for d in exp])
            buckets = {}
            for horizon in (30, 60, 90):
                buckets[str(horizon)] = int((days_to <= horizon).sum())
            out["expiry_risk_days"] = buckets
            if qty_col and cost_col:
                at_risk = qty[~pd.to_datetime(pd.to_datetime(df[expiry_col], errors="coerce").isna())]
                risk_value = round(
                    float((at_risk * numeric(df, cost_col).fillna(0)).sum()), 2)
                out["expiry_value_at_risk"] = risk_value
    if event_col or waste_cols:
        waste_mask = pd.Series(False, index=df.index)
        if event_col:
            waste_mask |= df[event_col].astype(str).str.lower().str.contains(
                "waste|writeoff|damage|expiry", na=False)
        for wc in waste_cols:
            waste_mask |= numeric(df, wc).gt(0)
        if bool(waste_mask.sum()):
            out["waste_rows"] = int(waste_mask.sum())
            if qty_col:
                out["waste_quantity"] = round(float(qty[waste_mask].sum()), 3)
    return out