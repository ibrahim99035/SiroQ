"""Best-effort domain analytics computed directly on an ingested dataframe.

These are pandas ports of the original SQL/SQLAlchemy analytics engines
(found in the pre-pivot ``app/services/analytics.py``). They never fabricate
data: an engine reports ``{"skipped": [...]}`` listing the canonical fields it
needed but could not find among the file's mapped columns.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd


def _pick(df: pd.DataFrame, fmap: dict, canonical: str, *candidates: str):
    """Resolve a source column for a canonical field, then fall back to
    candidate column names seen directly on the dataframe."""
    from_name = fmap.get(canonical)
    if from_name and from_name in df.columns:
        return from_name
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _sum(df: pd.DataFrame, col: str) -> float:
    return float(_numeric(df, col).sum())

# --- sales ---------------------------------------------------------------


def sales_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    amount_col = _pick(df, fmap, "total_amount", "total_amount", "total", "amount", "revenue")
    ts_col = _pick(df, fmap, "sale_timestamp", "sale_timestamp", "sale_date", "transaction_date", "datetime")
    tx_col = _pick(df, fmap, "transaction_ref", "transaction_ref", "transaction_id", "receipt_id", "invoice_no", "sale_id")
    pay_col = _pick(df, fmap, "payment_method", "payment_method", "payment_type", "payment")
    qty_col = _pick(df, fmap, "quantity", "quantity", "qty", "qty_sold")
    price_col = _pick(df, fmap, "unit_price", "unit_price", "price", "unit_cost")
    prod_col = _pick(df, fmap, "product_name", "product_name", "product", "item", "drug")
    cost_col = _pick(df, fmap, "cost_per_unit", "unit_cost", "product_cost", "cost")

    skipped = []
    if not amount_col:
        skipped.append("total_amount")
    if not ts_col:
        skipped.append("sale_timestamp")

    out: dict[str, Any] = {"category": "sales", "skipped": skipped}
    if amount_col:
        revenue = _sum(df, amount_col)
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
                .apply(_safe_sum)
            )
            out["payment_mix"] = [{"method": k, "amount": round(float(v), 2)}
                                  for k, v in paid.head(20).items()]
        if prod_col and revenue:
            by_prod = (
                df.assign(_g=df[prod_col].astype(str))
                .groupby("_g")[amount_col]
                .apply(_safe_sum)
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
            float((_numeric(df, qty_col) * _numeric(df, price_col)).sum()), 2)
    if qty_col and price_col and cost_col:
        revenue_lines = _numeric(df, qty_col) * _numeric(df, price_col)
        cost_lines = _numeric(df, qty_col) * _numeric(df, cost_col)
        margin = float((revenue_lines - cost_lines).sum())
        out["gross_margin"] = round(margin, 2)
    return out


def _safe_sum(s: pd.Series) -> float:
    return float(pd.to_numeric(s, errors="coerce").sum())

# --- inventory -----------------------------------------------------------


def inventory_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    batch_col = _pick(df, fmap, "batch_id", "batch_id", "batch", "lot", "lot_number")
    qty_col = _pick(df, fmap, "quantity", "quantity", "qty", "quantity_on_hand", "on_hand")
    expiry_col = _pick(df, fmap, "expiry_date", "expiry_date", "expiration_date", "exp")
    cost_col = _pick(df, fmap, "unit_cost", "cost_per_unit", "unit_price", "price")
    event_col = _pick(df, fmap, "event_type", "transaction_type")
    waste_cols = [c for c in df.columns
                  if any(t in c for t in ("waste", "writeoff", "damage", "damaged"))]

    skipped = []
    if not qty_col:
        skipped.append("quantity")
    if not expiry_col:
        skipped.append("expiry_date")

    out: dict[str, Any] = {"category": "inventory", "skipped": skipped}
    if qty_col:
        qty = _numeric(df, qty_col)
        out["quantity_on_hand"] = round(float(qty.sum()), 3)
        if cost_col:
            value = float((qty * _numeric(df, cost_col)).sum())
            out["stock_value"] = round(value, 2)
            # ABC/XYZ: cumulative-value share across lots -> A <=80%, B<=95%, C rest.
            if batch_col:
                lot_value = (qty * _numeric(df, cost_col)).groupby(
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
                    float((at_risk * _numeric(df, cost_col).fillna(0)).sum()), 2)
                out["expiry_value_at_risk"] = risk_value
    if event_col or waste_cols:
        waste_mask = pd.Series(False, index=df.index)
        if event_col:
            waste_mask |= df[event_col].astype(str).str.lower().str.contains(
                "waste|writeoff|damage|expiry", na=False)
        for wc in waste_cols:
            waste_mask |= _numeric(df, wc).gt(0)
        if bool(waste_mask.sum()):
            out["waste_rows"] = int(waste_mask.sum())
            if qty_col:
                out["waste_quantity"] = round(float(qty[waste_mask].sum()), 3)
    return out

# --- reference-entity engines --------------------------------------------


def _categorical_counts(df: pd.DataFrame, cols: list[str], limit: int = 10) -> dict:
    out = {}
    for col in cols:
        if col in df.columns:
            vc = df[col].astype(str).value_counts().head(limit)
            out[col] = [{"value": k, "count": int(v)} for k, v in vc.items()]
    return out


def product_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    name_col = _pick(df, fmap, "product_name", "product_name", "product", "item", "drug")
    ndc_col = _pick(df, fmap, "ndc", "sku", "product_code")
    out: dict[str, Any] = {"category": "products", "skipped": []}
    if name_col:
        names = df[name_col].dropna().astype(str).str.strip()
        out["product_count"] = int(names.nunique())
        out["sample_products"] = names.value_counts().head(10).to_dict()
    if ndc_col:
        ndc = df[ndc_col].dropna().astype(str)
        out["ndc_present"] = int(ndc.count())
        out["ndc_unique"] = int(ndc.nunique())
        bad = ndc.str.len() < 4
        if bool(bad.sum()):
            out["suspicious_ndc_count"] = int(bad.sum())
    out.update(_categorical_counts(df, ["category", "therapeutic_class", "form", "strength", "manufacturer", "supplier"]))
    return out


def patient_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    pid_col = _pick(df, fmap, "patient_id", "patient_id", "patient_hash", "patient_no", "member_no")
    out: dict[str, Any] = {"category": "patients", "skipped": []}
    if pid_col:
        ids = df[pid_col].dropna()
        out["patient_count"] = int(ids.nunique())
        out["records_with_id"] = int(ids.count())
    out.update(_categorical_counts(df, ["age_bracket", "gender", "chronic", "adherence_cohort", "insurance", "plan"]))
    return out


def prescription_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    rx_col = _pick(df, fmap, "rx_number", "rx_number", "prescription_id", "prescription_no", "rx")
    days_col = _pick(df, fmap, "days_supply", "quantity")
    out: dict[str, Any] = {"category": "prescriptions", "skipped": []}
    if rx_col:
        out["prescription_count"] = int(df[rx_col].nunique())
        out["fill_rows"] = int(len(df))
    if days_col:
        d = pd.to_numeric(df[days_col], errors="coerce").dropna()
        if len(d):
            out["days_supply"] = {
                "mean": round(float(d.mean()), 2),
                "median": round(float(d.median()), 2),
                "min": int(d.min()),
                "max": int(d.max()),
            }
    out.update(_categorical_counts(df, ["refill", "is_new_rx", "status", "controlled"]))
    return out


def payer_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    billed_col = _pick(df, fmap, "billed", "billed_amount", "amount_billed", "charge")
    paid_col = _pick(df, fmap, "paid", "paid_amount", "amount_paid", "reimbursement")
    rej_col = _pick(df, fmap, "rejection_code", "rejection_reason", "claim_status")
    out: dict[str, Any] = {"category": "payers", "skipped": []}
    if billed_col:
        billed = _sum(df, billed_col)
        out["total_billed"] = round(billed, 2)
        if paid_col:
            paid = _sum(df, paid_col)
            out["total_paid"] = round(paid, 2)
            out["collection_rate_pct"] = round(paid / billed * 100, 1) if billed else 0.0
        else:
            out["skipped"].append("paid_amount")
    elif paid_col:
        out["total_paid"] = round(_sum(df, paid_col), 2)
        out["skipped"].append("billed_amount")
    if rej_col:
        vc = df[rej_col].dropna().astype(str).value_counts().head(10)
        out["rejection_codes"] = [{"code": k, "count": int(v)} for k, v in vc.items()]
    out.update(_categorical_counts(df, ["payer", "payer_name", "plan_name", "insurance"]))
    return out


def supplier_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    sup_col = _pick(df, fmap, "supplier_name", "supplier_name", "supplier", "vendor")
    fill_col = _pick(df, fmap, "fill_rate")
    lead_col = _pick(df, fmap, "lead_time", "lead_time_days")
    out: dict[str, Any] = {"category": "suppliers", "skipped": []}
    if sup_col:
        out["supplier_count"] = int(df[sup_col].nunique())
    if fill_col:
        f = pd.to_numeric(df[fill_col], errors="coerce").dropna()
        if len(f):
            out["fill_rate"] = {"mean": round(float(f.mean()), 1), "min": float(f.min()),
                                "max": float(f.max()), "below_90_count": int((f < 90).sum())}
    if lead_col:
        l = pd.to_numeric(df[lead_col], errors="coerce").dropna()
        if len(l):
            out["lead_time_days"] = {"mean": round(float(l.mean()), 2), "max": int(l.max())}
    out.update(_categorical_counts(df, ["is_340b", "delivery_status", "on_time"]))
    return out


def purchase_order_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    po_col = _pick(df, fmap, "po_number", "purchase_order_id", "po", "order_no")
    ordered = _pick(df, fmap, "ordered_quantity", "order_quantity")
    received = _pick(df, fmap, "received_quantity")
    cost_col = _pick(df, fmap, "unit_cost")
    std_col = _pick(df, fmap, "standard_cost", "std_cost")
    out: dict[str, Any] = {"category": "purchase_orders", "skipped": []}
    if po_col:
        out["po_count"] = int(df[po_col].nunique())
    if ordered:
        o = _numeric(df, ordered)
        out["total_ordered_quantity"] = round(float(o.sum()), 3)
        if received:
            r = _numeric(df, received)
            rate = (r / o.replace(0, np.nan)).dropna()
            out["fill_rate"] = {
                "mean": round(float(rate.mean() * 100), 1),
                "below_90_count": int((rate * 100 < 90).sum()),
            }
    for col_name, key in ((cost_col, "total_po_value"),):
        if cost_col:
            if ordered:
                out[key] = round(float((_numeric(df, ordered) * _numeric(df, cost_col)).sum()), 2)
    if cost_col and std_col:
        variance = _numeric(df, cost_col) - _numeric(df, std_col)
        out["cost_variance_total"] = round(float(variance.fillna(0).sum()), 2)
    out.update(_categorical_counts(df, ["status", "delivery_status", "supplier"]))
    return out


_REGISTRY: dict[str, Callable[[pd.DataFrame, dict], dict]] = {
    "sales": sales_analytics,
    "inventory": inventory_analytics,
    "products": product_analytics,
    "patients": patient_analytics,
    "prescriptions": prescription_analytics,
    "payers": payer_analytics,
    "suppliers": supplier_analytics,
    "purchase_orders": purchase_order_analytics,
}


def run_domain_analytics(df: pd.DataFrame, category: str, fmap: dict) -> dict[str, Any]:
    engine = _REGISTRY.get(category)
    if engine is None:
        return {"skipped": f"no domain engine for category {category!r}"}
    try:
        return engine(df, fmap)
    except Exception as exc:  # defensive — a bad frame must not kill the run
        return {"error": f"{type(exc).__name__}: {exc}"}