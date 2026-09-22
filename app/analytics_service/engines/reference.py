"""Referential / master-data domain analytics: products, patients,
prescriptions, payers, suppliers."""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics_service.engines._helpers import categorical_counts, col_sum, pick_col
from app.analytics_service.registry import domain_engine


@domain_engine("products")
def product_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    name_col = pick_col(df, fmap, "product_name", "product_name", "product", "item", "drug")
    ndc_col = pick_col(df, fmap, "ndc", "sku", "product_code")
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
    out.update(categorical_counts(df, ["category", "therapeutic_class", "form", "strength", "manufacturer", "supplier"]))
    return out


@domain_engine("patients")
def patient_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    pid_col = pick_col(df, fmap, "patient_id", "patient_id", "patient_hash", "patient_no", "member_no")
    out: dict[str, Any] = {"category": "patients", "skipped": []}
    if pid_col:
        ids = df[pid_col].dropna()
        out["patient_count"] = int(ids.nunique())
        out["records_with_id"] = int(ids.count())
    out.update(categorical_counts(df, ["age_bracket", "gender", "chronic", "adherence_cohort", "insurance", "plan"]))
    return out


@domain_engine("prescriptions")
def prescription_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    rx_col = pick_col(df, fmap, "rx_number", "rx_number", "prescription_id", "prescription_no", "rx")
    days_col = pick_col(df, fmap, "days_supply", "quantity")
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
    out.update(categorical_counts(df, ["refill", "is_new_rx", "status", "controlled"]))
    return out


@domain_engine("payers")
def payer_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    billed_col = pick_col(df, fmap, "billed", "billed_amount", "amount_billed", "charge")
    paid_col = pick_col(df, fmap, "paid", "paid_amount", "amount_paid", "reimbursement")
    rej_col = pick_col(df, fmap, "rejection_code", "rejection_reason", "claim_status")
    out: dict[str, Any] = {"category": "payers", "skipped": []}
    if billed_col:
        billed = col_sum(df, billed_col)
        out["total_billed"] = round(billed, 2)
        if paid_col:
            paid = col_sum(df, paid_col)
            out["total_paid"] = round(paid, 2)
            out["collection_rate_pct"] = round(paid / billed * 100, 1) if billed else 0.0
        else:
            out["skipped"].append("paid_amount")
    elif paid_col:
        out["total_paid"] = round(col_sum(df, paid_col), 2)
        out["skipped"].append("billed_amount")
    if rej_col:
        vc = df[rej_col].dropna().astype(str).value_counts().head(10)
        out["rejection_codes"] = [{"code": k, "count": int(v)} for k, v in vc.items()]
    out.update(categorical_counts(df, ["payer", "payer_name", "plan_name", "insurance"]))
    return out


@domain_engine("suppliers")
def supplier_analytics(df: pd.DataFrame, fmap: dict) -> dict[str, Any]:
    sup_col = pick_col(df, fmap, "supplier_name", "supplier_name", "supplier", "vendor")
    fill_col = pick_col(df, fmap, "fill_rate")
    lead_col = pick_col(df, fmap, "lead_time", "lead_time_days")
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
    out.update(categorical_counts(df, ["is_340b", "delivery_status", "on_time"]))
    return out