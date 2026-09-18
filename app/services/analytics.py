"""Analytics aggregation helpers for the dashboards. Scope-aware via RLS; the
queries only ever see rows in the caller's tenant (and pharmacy scope).
"""
from datetime import datetime, timedelta
from sqlalchemy import func, case, and_, or_, text, desc, distinct, extract
from sqlalchemy.orm import aliased

from app.models.models import (
    Sales,
    SaleLines,
    InventoryEvents,
    Batches,
    Products,
    Prescriptions,
    Patients,
    Payers,
    Suppliers,
    PurchaseOrders,
    DailySalesSnapshot,
    DailyInventorySnapshot,
    DailyAdherenceSnapshot,
    DailySupplierSnapshot,
    AlertRules,
    Alerts,
)


def sales_kpis(db, association_id, pharmacy_id=None):
    q = db.query(
        func.count(Sales.id),
        func.coalesce(func.sum(Sales.total_amount), 0),
    ).filter(Sales.association_id == association_id)
    if pharmacy_id:
        q = q.filter(Sales.pharmacy_id == pharmacy_id)
    tx_count, revenue = q.one()
    avg = float(revenue) / tx_count if tx_count else 0.0

    rows = (
        db.query(
            func.date(Sales.sale_timestamp).label("day"),
            func.coalesce(func.sum(Sales.total_amount), 0).label("rev"),
        )
        .filter(Sales.association_id == association_id)
        .group_by(func.date(Sales.sale_timestamp))
        .order_by(func.date(Sales.sale_timestamp))
        .all()
    )
    labels = [str(r.day) for r in rows]
    revenues = [float(r.rev) for r in rows]
    return {
        "total_revenue": float(revenue),
        "transactions": tx_count,
        "avg_basket": avg,
        "labels": labels or ["All"],
        "revenues": revenues or [0.0],
    }


def inventory_kpis(db, association_id, pharmacy_id=None):
    events = (
        db.query(InventoryEvents)
        .filter(InventoryEvents.association_id == association_id)
        .all()
    )
    waste = sum(float(e.quantity) for e in events if e.event_type in ("expiry_writeoff", "damage"))
    units_near_expiry = db.query(func.count(Batches.id)).join(
        InventoryEvents, InventoryEvents.batch_id == Batches.id
    ).filter(
        Batches.association_id == association_id,
        Batches.expiry_date.isnot(None),
    ).scalar() or 0

    sales_units = (
        db.query(func.coalesce(func.sum(SaleLines.quantity), 0))
        .join(Sales, Sales.id == SaleLines.sale_id)
        .filter(Sales.association_id == association_id)
        .scalar()
    ) or 0

    product_labels = []
    waste_data = []
    for e in events:
        if e.event_type in ("expiry_writeoff", "damage"):
            product_labels.append(str(e.batch_id)[:8])
            waste_data.append(float(e.quantity))

    return {
        "units_near_expiry": units_near_expiry,
        "waste_cost": waste,
        "turnover": float(sales_units) / max(waste, 1.0),
        "product_labels": product_labels or ["None"],
        "waste_data": waste_data or [0.0],
    }


def inventory_optimization_analytics(db, association_id, pharmacy_id=None):
    """Comprehensive inventory optimization analytics including days-of-supply,
    turnover, dead stock, expiry risk, reorder points, and ABC/XYZ classification."""
    from datetime import date, timedelta

    today = date.today()
    thirty_days_ago = today - timedelta(days=30)
    sixty_days_ago = today - timedelta(days=60)
    ninety_days_ago = today - timedelta(days=90)

    # Base query for batches in this association
    batch_q = db.query(Batches).filter(Batches.association_id == association_id)
    if pharmacy_id:
        # Filter by pharmacy through inventory events
        batch_q = batch_q.join(InventoryEvents, InventoryEvents.batch_id == Batches.id).filter(
            InventoryEvents.pharmacy_id == pharmacy_id
        )

    batches = batch_q.all()

    # Calculate days of supply for each product
    product_metrics = {}
    for batch in batches:
        pid = batch.product_id
        if pid not in product_metrics:
            product = db.query(Products).filter(Products.id == pid).first()
            product_metrics[pid] = {
                "product_id": pid,
                "product_name": product.canonical_name or product.raw_name if product else "Unknown",
                "ndc": product.ndc if product else None,
                "therapeutic_class": product.therapeutic_class if product else None,
                "controlled_schedule": product.controlled_schedule if product else None,
                "total_qty_on_hand": 0,
                "total_value_on_hand": 0,
                "avg_daily_demand": 0,
                "days_of_supply": 0,
                "turnover_rate": 0,
                "expiry_risk_30": 0,
                "expiry_risk_60": 0,
                "expiry_risk_90": 0,
                "expiry_value_at_risk_30": 0,
                "expiry_value_at_risk_60": 0,
                "expiry_value_at_risk_90": 0,
                "is_dead_stock": False,
                "last_movement_date": None,
                "reorder_point": 0,
                "safety_stock": 0,
                "abc_class": "C",
                "xyz_class": "Z",
            }

        m = product_metrics[pid]
        qty = float(batch.quantity_on_hand)
        cost = float(batch.cost_basis) if batch.cost_basis else 0
        m["total_qty_on_hand"] += qty
        m["total_value_on_hand"] += qty * cost

        # Expiry risk
        if batch.expiry_date:
            expiry_date = batch.expiry_date.date() if hasattr(batch.expiry_date, 'date') else batch.expiry_date
            days_to_expiry = (expiry_date - today).days
            if days_to_expiry <= 30:
                m["expiry_risk_30"] += qty
                m["expiry_value_at_risk_30"] += qty * cost
            if days_to_expiry <= 60:
                m["expiry_risk_60"] += qty
                m["expiry_value_at_risk_60"] += qty * cost
            if days_to_expiry <= 90:
                m["expiry_risk_90"] += qty
                m["expiry_value_at_risk_90"] += qty * cost

    # Calculate daily demand from sales (last 90 days)
    sales_q = db.query(
        SaleLines.product_id,
        func.coalesce(func.sum(SaleLines.quantity), 0).label("total_qty"),
        func.count(distinct(func.date(Sales.sale_timestamp))).label("active_days"),
    ).join(Sales, Sales.id == SaleLines.sale_id).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= ninety_days_ago,
    )
    if pharmacy_id:
        sales_q = sales_q.filter(Sales.pharmacy_id == pharmacy_id)
    sales_q = sales_q.group_by(SaleLines.product_id).all()

    for row in sales_q:
        pid = row.product_id
        if pid in product_metrics:
            active_days = max(row.active_days, 1)
            product_metrics[pid]["avg_daily_demand"] = float(row.total_qty) / active_days
            product_metrics[pid]["last_movement_date"] = today

    # Calculate days of supply, turnover, dead stock
    for pid, m in product_metrics.items():
        if m["avg_daily_demand"] > 0:
            m["days_of_supply"] = m["total_qty_on_hand"] / m["avg_daily_demand"]
            m["turnover_rate"] = (m["avg_daily_demand"] * 365) / max(m["total_qty_on_hand"], 1)
        else:
            m["days_of_supply"] = 999 if m["total_qty_on_hand"] > 0 else 0
            m["turnover_rate"] = 0

        # Dead stock: no movement in 90 days
        if m["avg_daily_demand"] == 0 and m["total_qty_on_hand"] > 0:
            m["is_dead_stock"] = True

    # ABC Classification by value (annualized)
    sorted_by_value = sorted(
        product_metrics.values(),
        key=lambda x: x["total_value_on_hand"],
        reverse=True
    )
    total_value = sum(m["total_value_on_hand"] for m in sorted_by_value)
    cum_value = 0
    for m in sorted_by_value:
        cum_value += m["total_value_on_hand"]
        pct = cum_value / max(total_value, 1)
        if pct <= 0.8:
            m["abc_class"] = "A"
        elif pct <= 0.95:
            m["abc_class"] = "B"
        else:
            m["abc_class"] = "C"

    # XYZ Classification by demand variability (coefficient of variation)
    # For simplicity, use turnover as proxy - high turnover = X, low = Z
    for m in product_metrics.values():
        if m["turnover_rate"] >= 12:
            m["xyz_class"] = "X"
        elif m["turnover_rate"] >= 4:
            m["xyz_class"] = "Y"
        else:
            m["xyz_class"] = "Z"

    # Reorder point calculation (demand forecast + safety stock)
    for m in product_metrics.values():
        lead_time = 7  # Default 7 days lead time, could come from supplier
        service_level_z = 1.65  # 95% service level
        demand_std = m["avg_daily_demand"] * 0.3  # Simplified assumption
        m["safety_stock"] = service_level_z * demand_std * (lead_time ** 0.5)
        m["reorder_point"] = m["avg_daily_demand"] * lead_time + m["safety_stock"]

    # Controlled substance reconciliation
    controlled_batches = [b for b in batches if b.controlled_schedule]
    controlled_variance = 0
    for batch in controlled_batches:
        # Compare system quantity vs physical count (from latest inventory event)
        latest_event = db.query(InventoryEvents).filter(
            InventoryEvents.batch_id == batch.id
        ).order_by(desc(InventoryEvents.event_timestamp)).first()
        if latest_event and latest_event.event_type == "physical_count":
            variance = abs(float(batch.quantity_on_hand) - float(latest_event.quantity))
            controlled_variance += variance

    # Summary metrics
    total_inventory_value = sum(m["total_value_on_hand"] for m in product_metrics.values())
    total_near_expiry_30 = sum(m["expiry_value_at_risk_30"] for m in product_metrics.values())
    total_near_expiry_60 = sum(m["expiry_value_at_risk_60"] for m in product_metrics.values())
    total_near_expiry_90 = sum(m["expiry_value_at_risk_90"] for m in product_metrics.values())
    total_dead_stock_value = sum(
        m["total_value_on_hand"] for m in product_metrics.values() if m["is_dead_stock"]
    )
    dead_stock_count = sum(1 for m in product_metrics.values() if m["is_dead_stock"])

    return {
        "product_metrics": list(product_metrics.values()),
        "summary": {
            "total_inventory_value": total_inventory_value,
            "total_products": len(product_metrics),
            "expiry_value_at_risk_30": total_near_expiry_30,
            "expiry_value_at_risk_60": total_near_expiry_60,
            "expiry_value_at_risk_90": total_near_expiry_90,
            "dead_stock_count": dead_stock_count,
            "dead_stock_value": total_dead_stock_value,
            "controlled_substance_variance": controlled_variance,
            "products_needing_reorder": sum(1 for m in product_metrics.values() 
                if m["total_qty_on_hand"] <= m["reorder_point"] and m["avg_daily_demand"] > 0),
        }
    }


def abc_xyz_classification(db, association_id, pharmacy_id=None):
    """Get ABC/XYZ classification matrix for inventory segmentation."""
    inv_analytics = inventory_optimization_analytics(db, association_id, pharmacy_id)
    
    matrix = {
        "AX": [], "AY": [], "AZ": [],
        "BX": [], "BY": [], "BZ": [],
        "CX": [], "CY": [], "CZ": [],
    }
    
    for m in inv_analytics["product_metrics"]:
        key = f"{m['abc_class']}{m['xyz_class']}"
        if key in matrix:
            matrix[key].append({
                "product_id": m["product_id"],
                "product_name": m["product_name"],
                "total_value": m["total_value_on_hand"],
                "turnover_rate": m["turnover_rate"],
                "days_of_supply": m["days_of_supply"],
            })
    
    return {
        "matrix": matrix,
        "counts": {k: len(v) for k, v in matrix.items()},
        "replenishment_policy": {
            "AX": "Continuous review, auto-reorder, high service level",
            "AY": "Continuous review, safety stock for variability",
            "AZ": "Periodic review, higher safety stock",
            "BX": "Continuous review, moderate service level",
            "BY": "Periodic review, standard safety stock",
            "BZ": "Periodic review, higher safety stock",
            "CX": "Simple min/max, low cost",
            "CY": "Periodic review, low cost",
            "CZ": "Bulk ordering, accept stockouts",
        }
    }


def sales_margin_analytics(db, association_id, pharmacy_id=None, start_date=None, end_date=None):
    """Comprehensive sales, margin, and revenue integrity analytics."""
    from datetime import date, timedelta

    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    # Base sales query
    sales_q = db.query(Sales).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
    )
    if pharmacy_id:
        sales_q = sales_q.filter(Sales.pharmacy_id == pharmacy_id)

    # Revenue by payment method (cash vs insurance)
    payment_revenue = db.query(
        Sales.payment_method,
        func.coalesce(func.sum(Sales.total_amount), 0).label("revenue"),
        func.count(Sales.id).label("count"),
    ).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
    )
    if pharmacy_id:
        payment_revenue = payment_revenue.filter(Sales.pharmacy_id == pharmacy_id)
    payment_revenue = payment_revenue.group_by(Sales.payment_method).all()

    cash_revenue = sum(float(r.revenue) for r in payment_revenue if r.payment_method and "cash" in r.payment_method.lower())
    insurance_revenue = sum(float(r.revenue) for r in payment_revenue if r.payment_method and "cash" not in r.payment_method.lower())
    total_revenue = sum(float(r.revenue) for r in payment_revenue)
    total_transactions = sum(r.count for r in payment_revenue)

    # Margin analysis at line level
    margin_q = db.query(
        SaleLines.product_id,
        Products.ndc,
        Products.canonical_name,
        Products.cost_per_unit,
        SaleLines.unit_price,
        SaleLines.quantity,
        func.sum(SaleLines.quantity * SaleLines.unit_price).label("line_revenue"),
        func.sum(SaleLines.quantity * Products.cost_per_unit).label("line_cost"),
    ).join(Sales, Sales.id == SaleLines.sale_id).join(
        Products, Products.id == SaleLines.product_id
    ).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
        Products.cost_per_unit.isnot(None),
    )
    if pharmacy_id:
        margin_q = margin_q.filter(Sales.pharmacy_id == pharmacy_id)
    margin_q = margin_q.group_by(
        SaleLines.product_id, Products.ndc, Products.canonical_name,
        Products.cost_per_unit, SaleLines.unit_price, SaleLines.quantity
    ).all()

    gross_margin = 0
    total_cogs = 0
    product_margins = []
    for row in margin_q:
        revenue = float(row.line_revenue) if row.line_revenue else 0
        cost = float(row.line_cost) if row.line_cost else 0
        margin = revenue - cost
        margin_pct = (margin / revenue * 100) if revenue > 0 else 0
        gross_margin += margin
        total_cogs += cost
        product_margins.append({
            "product_id": row.product_id,
            "ndc": row.ndc,
            "product_name": row.canonical_name,
            "revenue": revenue,
            "cost": cost,
            "margin": margin,
            "margin_pct": margin_pct,
        })

    # Payer mix analysis
    payer_q = db.query(
        SaleLines.extra_attributes["payer_id"].label("payer_id"),
        Payers.payer_name,
        Payers.plan_name,
        func.coalesce(func.sum(SaleLines.quantity * SaleLines.unit_price), 0).label("revenue"),
        func.count(Sales.id).label("claim_count"),
    ).join(Sales, Sales.id == SaleLines.sale_id).outerjoin(
        Payers, Payers.id == SaleLines.extra_attributes["payer_id"]
    ).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
    )
    if pharmacy_id:
        payer_q = payer_q.filter(Sales.pharmacy_id == pharmacy_id)
    payer_q = payer_q.group_by(
        SaleLines.extra_attributes["payer_id"], Payers.payer_name, Payers.plan_name
    ).all()

    payer_mix = []
    for row in payer_q:
        revenue = float(row.revenue) if row.revenue else 0
        payer_mix.append({
            "payer_id": row.payer_id,
            "payer_name": row.payer_name or "Unknown",
            "plan_name": row.plan_name,
            "revenue": revenue,
            "claim_count": row.claim_count,
            "pct_of_total": (revenue / total_revenue * 100) if total_revenue > 0 else 0,
        })

    # Rejection analysis (from extra_attributes)
    rejection_q = db.query(
        SaleLines.extra_attributes["rejection_code"].label("rejection_code"),
        func.count(Sales.id).label("count"),
    ).join(Sales, Sales.id == SaleLines.sale_id).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
        SaleLines.extra_attributes["rejection_code"].isnot(None),
    )
    if pharmacy_id:
        rejection_q = rejection_q.filter(Sales.pharmacy_id == pharmacy_id)
    rejection_q = rejection_q.group_by(SaleLines.extra_attributes["rejection_code"]).all()

    rejections = []
    for row in rejection_q:
        rejections.append({
            "rejection_code": row.rejection_code,
            "count": row.count,
        })

    # Prescription vs OTC split
    rx_vs_otc = db.query(
        SaleLines.extra_attributes["is_prescription"].label("is_rx"),
        func.coalesce(func.sum(SaleLines.quantity * SaleLines.unit_price), 0).label("revenue"),
        func.count(Sales.id).label("count"),
    ).join(Sales, Sales.id == SaleLines.sale_id).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
    )
    if pharmacy_id:
        rx_vs_otc = rx_vs_otc.filter(Sales.pharmacy_id == pharmacy_id)
    rx_vs_otc = rx_vs_otc.group_by(SaleLines.extra_attributes["is_prescription"]).all()

    rx_revenue = 0
    otc_revenue = 0
    rx_count = 0
    otc_count = 0
    for row in rx_vs_otc:
        revenue = float(row.revenue) if row.revenue else 0
        if row.is_rx:
            rx_revenue = revenue
            rx_count = row.count
        else:
            otc_revenue = revenue
            otc_count = row.count

    # New vs refill split
    new_vs_refill = db.query(
        SaleLines.extra_attributes["is_new_rx"].label("is_new"),
        func.coalesce(func.sum(SaleLines.quantity * SaleLines.unit_price), 0).label("revenue"),
        func.count(Sales.id).label("count"),
    ).join(Sales, Sales.id == SaleLines.sale_id).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
        SaleLines.extra_attributes["is_prescription"] == True,
    )
    if pharmacy_id:
        new_vs_refill = new_vs_refill.filter(Sales.pharmacy_id == pharmacy_id)
    new_vs_refill = new_vs_refill.group_by(SaleLines.extra_attributes["is_new_rx"]).all()

    new_revenue = 0
    refill_revenue = 0
    for row in new_vs_refill:
        revenue = float(row.revenue) if row.revenue else 0
        if row.is_new:
            new_revenue = revenue
        else:
            refill_revenue = revenue

    # Net margin estimation (after DIR fees, estimated)
    estimated_dir_fees = total_revenue * 0.03  # Rough estimate 3%
    net_margin = gross_margin - estimated_dir_fees

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "revenue": {
            "total": total_revenue,
            "cash": cash_revenue,
            "insurance": insurance_revenue,
            "cash_pct": (cash_revenue / total_revenue * 100) if total_revenue > 0 else 0,
        },
        "transactions": total_transactions,
        "avg_basket": total_revenue / total_transactions if total_transactions > 0 else 0,
        "margins": {
            "gross_margin": gross_margin,
            "gross_margin_pct": (gross_margin / total_revenue * 100) if total_revenue > 0 else 0,
            "total_cogs": total_cogs,
            "estimated_net_margin": net_margin,
            "estimated_net_margin_pct": (net_margin / total_revenue * 100) if total_revenue > 0 else 0,
            "estimated_dir_fees": estimated_dir_fees,
        },
        "product_margins": sorted(product_margins, key=lambda x: x["margin"], reverse=True)[:50],
        "payer_mix": sorted(payer_mix, key=lambda x: x["revenue"], reverse=True),
        "rejections": sorted(rejections, key=lambda x: x["count"], reverse=True),
        "rx_vs_otc": {
            "rx": {"revenue": rx_revenue, "count": rx_count},
            "otc": {"revenue": otc_revenue, "count": otc_count},
        },
        "new_vs_refill": {
            "new": {"revenue": new_revenue},
            "refill": {"revenue": refill_revenue},
        },
    }


def payer_performance_analytics(db, association_id, pharmacy_id=None, start_date=None, end_date=None):
    """Detailed payer performance with rejection root-cause analysis."""
    from datetime import date, timedelta

    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=30)

    # Get all claims with payer info
    claims_q = db.query(
        SaleLines.extra_attributes["payer_id"].label("payer_id"),
        SaleLines.extra_attributes["claim_status"].label("claim_status"),
        SaleLines.extra_attributes["rejection_code"].label("rejection_code"),
        SaleLines.extra_attributes["rejection_reason"].label("rejection_reason"),
        Payers.payer_name,
        Payers.plan_name,
        Payers.contract_rate,
        Payers.dir_fee_rate,
        func.coalesce(func.sum(SaleLines.quantity * SaleLines.unit_price), 0).label("billed_amount"),
        func.coalesce(func.sum(SaleLines.extra_attributes["paid_amount"]), 0).label("paid_amount"),
        func.count(Sales.id).label("claim_count"),
    ).join(Sales, Sales.id == SaleLines.sale_id).outerjoin(
        Payers, Payers.id == SaleLines.extra_attributes["payer_id"]
    ).filter(
        Sales.association_id == association_id,
        func.date(Sales.sale_timestamp) >= start_date,
        func.date(Sales.sale_timestamp) <= end_date,
        SaleLines.extra_attributes["is_prescription"] == True,
    )
    if pharmacy_id:
        claims_q = claims_q.filter(Sales.pharmacy_id == pharmacy_id)
    claims_q = claims_q.group_by(
        SaleLines.extra_attributes["payer_id"],
        SaleLines.extra_attributes["claim_status"],
        SaleLines.extra_attributes["rejection_code"],
        SaleLines.extra_attributes["rejection_reason"],
        Payers.payer_name,
        Payers.plan_name,
        Payers.contract_rate,
        Payers.dir_fee_rate,
    ).all()

    payer_performance = {}
    for row in claims_q:
        pid = row.payer_id or "unknown"
        if pid not in payer_performance:
            payer_performance[pid] = {
                "payer_id": pid,
                "payer_name": row.payer_name or "Unknown",
                "plan_name": row.plan_name,
                "contract_rate": float(row.contract_rate) if row.contract_rate else 0,
                "dir_fee_rate": float(row.dir_fee_rate) if row.dir_fee_rate else 0,
                "total_billed": 0,
                "total_paid": 0,
                "total_claims": 0,
                "paid_claims": 0,
                "rejected_claims": 0,
                "rejections": {},
            }
        p = payer_performance[pid]
        billed = float(row.billed_amount) if row.billed_amount else 0
        paid = float(row.paid_amount) if row.paid_amount else 0
        p["total_billed"] += billed
        p["total_paid"] += paid
        p["total_claims"] += row.claim_count
        if row.claim_status == "paid":
            p["paid_claims"] += row.claim_count
        elif row.claim_status == "rejected":
            p["rejected_claims"] += row.claim_count
            code = row.rejection_code or "unknown"
            p["rejections"][code] = p["rejections"].get(code, 0) + row.claim_count

    # Calculate rates
    for p in payer_performance.values():
        p["paid_rate"] = (p["paid_claims"] / p["total_claims"] * 100) if p["total_claims"] > 0 else 0
        p["rejection_rate"] = (p["rejected_claims"] / p["total_claims"] * 100) if p["total_claims"] > 0 else 0
        p["collection_rate"] = (p["total_paid"] / p["total_billed"] * 100) if p["total_billed"] > 0 else 0
        p["top_rejections"] = sorted(p["rejections"].items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "payers": sorted(payer_performance.values(), key=lambda x: x["total_billed"], reverse=True),
        "summary": {
            "total_payers": len(payer_performance),
            "overall_paid_rate": sum(p["paid_claims"] for p in payer_performance.values()) / 
                max(sum(p["total_claims"] for p in payer_performance.values()), 1) * 100,
            "overall_rejection_rate": sum(p["rejected_claims"] for p in payer_performance.values()) / 
                max(sum(p["total_claims"] for p in payer_performance.values()), 1) * 100,
        }
    }


def adherence_analytics(db, association_id, pharmacy_id=None, window_days=90):
    """Prescription adherence analytics: PDC, MPR, refill gaps, high-risk cohorts."""
    from datetime import date, timedelta

    today = date.today()
    window_start = today - timedelta(days=window_days)

    # Get active prescriptions in window
    rx_q = db.query(Prescriptions).filter(
        Prescriptions.association_id == association_id,
        Prescriptions.is_active == True,
        Prescriptions.original_rx_date <= today,
    )
    if pharmacy_id:
        # Filter by pharmacy through prescriber or patient association
        rx_q = rx_q.join(Patients, Patients.id == Prescriptions.patient_id).filter(
            # Patient's pharmacy could be tracked differently
        )
    prescriptions = rx_q.all()

    patient_adherence = {}
    for rx in prescriptions:
        pid = rx.patient_id
        product_id = rx.product_id

        if pid not in patient_adherence:
            patient = db.query(Patients).filter(Patients.id == pid).first()
            patient_adherence[pid] = {
                "patient_id": pid,
                "patient_hash": patient.patient_hash if patient else None,
                "age_bracket": patient.age_bracket if patient else None,
                "gender": patient.gender if patient else None,
                "chronic_conditions": patient.chronic_conditions if patient else [],
                "adherence_cohort": patient.adherence_cohort if patient else None,
                "prescriptions": [],
                "pdc_30": 0,
                "pdc_90": 0,
                "mpr_30": 0,
                "mpr_90": 0,
                "days_since_last_fill": None,
                "refill_gap_days": None,
                "is_adherent_80": False,
                "risk_level": "low",
            }

        # Get fills for this prescription in window
        fills = db.query(SaleLines).join(Sales, Sales.id == SaleLines.sale_id).filter(
            Sales.association_id == association_id,
            SaleLines.product_id == product_id,
            SaleLines.extra_attributes["prescription_id"].astext == rx.rx_number,
            func.date(Sales.sale_timestamp) >= window_start,
            func.date(Sales.sale_timestamp) <= today,
        )
        if pharmacy_id:
            fills = fills.filter(Sales.pharmacy_id == pharmacy_id)
        fills = fills.order_by(Sales.sale_timestamp).all()

        fill_dates = [f.sale.sale_timestamp.date() for f in fills if f.sale]
        days_supply_per_fill = [f.extra_attributes.get("days_supply", rx.days_supply) for f in fills]

        # Calculate PDC (Proportion of Days Covered)
        covered_days_30 = 0
        covered_days_90 = 0
        for i, fill_date in enumerate(fill_dates):
            days_supply = days_supply_per_fill[i] if i < len(days_supply_per_fill) else rx.days_supply
            end_cover = fill_date + timedelta(days=days_supply)
            # 30-day window
            if fill_date >= today - timedelta(days=30):
                covered_days_30 += min((end_cover - fill_date).days, 30)
            # 90-day window
            if fill_date >= window_start:
                covered_days_90 += min((end_cover - fill_date).days, 90)

        pdc_30 = covered_days_30 / 30 if window_days >= 30 else 0
        pdc_90 = covered_days_90 / window_days

        # Calculate MPR (Medication Possession Ratio)
        total_days_supply = sum(days_supply_per_fill)
        mpr_30 = min(total_days_supply / 30, 1.0) if window_days >= 30 else 0
        mpr_90 = min(total_days_supply / window_days, 1.0)

        # Days since last fill
        days_since_last = (today - fill_dates[-1]).days if fill_dates else None

        # Refill gap (if expected refill date passed)
        refill_gap = None
        if fill_dates and rx.refills_remaining > 0:
            last_fill = fill_dates[-1]
            expected_refill = last_fill + timedelta(days=rx.days_supply)
            if expected_refill < today:
                refill_gap = (today - expected_refill).days

        patient_adherence[pid]["prescriptions"].append({
            "rx_number": rx.rx_number,
            "product_id": product_id,
            "days_supply": rx.days_supply,
            "refills_remaining": rx.refills_remaining,
            "fills_in_window": len(fills),
            "pdc_30": pdc_30,
            "pdc_90": pdc_90,
            "mpr_30": mpr_30,
            "mpr_90": mpr_90,
            "days_since_last_fill": days_since_last,
            "refill_gap_days": refill_gap,
        })

        # Aggregate at patient level (worst PDC across their prescriptions)
        patient_adherence[pid]["pdc_30"] = min(patient_adherence[pid]["pdc_30"], pdc_30) if patient_adherence[pid]["pdc_30"] > 0 else pdc_30
        patient_adherence[pid]["pdc_90"] = min(patient_adherence[pid]["pdc_90"], pdc_90) if patient_adherence[pid]["pdc_90"] > 0 else pdc_90
        patient_adherence[pid]["mpr_30"] = min(patient_adherence[pid]["mpr_30"], mpr_30) if patient_adherence[pid]["mpr_30"] > 0 else mpr_30
        patient_adherence[pid]["mpr_90"] = min(patient_adherence[pid]["mpr_90"], mpr_90) if patient_adherence[pid]["mpr_90"] > 0 else mpr_90

        if days_since_last is not None:
            if patient_adherence[pid]["days_since_last_fill"] is None or days_since_last < patient_adherence[pid]["days_since_last_fill"]:
                patient_adherence[pid]["days_since_last_fill"] = days_since_last

        if refill_gap is not None:
            if patient_adherence[pid]["refill_gap_days"] is None or refill_gap > patient_adherence[pid]["refill_gap_days"]:
                patient_adherence[pid]["refill_gap_days"] = refill_gap

    # Determine adherence status and risk level
    high_risk_patients = []
    for pid, p in patient_adherence.items():
        p["is_adherent_80"] = p["pdc_90"] >= 0.8
        
        # Risk stratification
        risk_score = 0
        if p["pdc_90"] < 0.5:
            risk_score += 3
        elif p["pdc_90"] < 0.8:
            risk_score += 2
        elif p["pdc_90"] < 0.9:
            risk_score += 1

        if p["refill_gap_days"] and p["refill_gap_days"] > 14:
            risk_score += 2
        elif p["refill_gap_days"] and p["refill_gap_days"] > 7:
            risk_score += 1

        # Chronic conditions increase risk
        if p["chronic_conditions"]:
            risk_score += len(p["chronic_conditions"])

        if risk_score >= 5:
            p["risk_level"] = "high"
            high_risk_patients.append(p)
        elif risk_score >= 3:
            p["risk_level"] = "medium"
        else:
            p["risk_level"] = "low"

    # Cohort analysis
    adherent_count = sum(1 for p in patient_adherence.values() if p["is_adherent_80"])
    total_patients = len(patient_adherence)
    high_risk_count = len(high_risk_patients)

    return {
        "window_days": window_days,
        "total_patients": total_patients,
        "adherent_patients": adherent_count,
        "adherence_rate": (adherent_count / total_patients * 100) if total_patients > 0 else 0,
        "high_risk_patients": high_risk_count,
        "patient_details": list(patient_adherence.values()),
        "cohort_breakdown": {
            "high_risk": high_risk_count,
            "medium_risk": sum(1 for p in patient_adherence.values() if p["risk_level"] == "medium"),
            "low_risk": sum(1 for p in patient_adherence.values() if p["risk_level"] == "low"),
        },
        "actionable": [
            {
                "patient_id": p["patient_id"],
                "patient_hash": p["patient_hash"],
                "risk_level": p["risk_level"],
                "pdc_90": p["pdc_90"],
                "refill_gap_days": p["refill_gap_days"],
                "chronic_conditions": p["chronic_conditions"],
                "recommended_action": "Contact for refill" if p["refill_gap_days"] else "Adherence counseling"
            }
            for p in high_risk_patients
        ][:20],  # Top 20 actionable
    }


def controlled_substance_analytics(db, association_id, pharmacy_id=None):
    """Controlled substance utilization patterns and red flag detection."""
    from datetime import date, timedelta

    today = date.today()
    ninety_days_ago = today - timedelta(days=90)

    # Get controlled substance products
    controlled_products = db.query(Products).filter(
        Products.association_id == association_id,
        Products.controlled_schedule.isnot(None),
    ).all()

    controlled_product_ids = [p.id for p in controlled_products]

    if not controlled_product_ids:
        return {"message": "No controlled substances found", "alerts": []}

    # Get dispensing data for controlled substances
    dispenses = db.query(
        SaleLines,
        Sales,
        Patients,
        Products,
    ).join(Sales, Sales.id == SaleLines.sale_id).join(
        Patients, Patients.id == SaleLines.extra_attributes["patient_id"]
    ).join(Products, Products.id == SaleLines.product_id).filter(
        Sales.association_id == association_id,
        SaleLines.product_id.in_(controlled_product_ids),
        func.date(Sales.sale_timestamp) >= ninety_days_ago,
    )
    if pharmacy_id:
        dispenses = dispenses.filter(Sales.pharmacy_id == pharmacy_id)
    dispenses = dispenses.all()

    # Analyze by patient
    patient_patterns = {}
    for sl, sale, patient, product in dispenses:
        pid = patient.id if patient else "unknown"
        if pid not in patient_patterns:
            patient_patterns[pid] = {
                "patient_id": pid,
                "patient_hash": patient.patient_hash if patient else None,
                "prescribers": set(),
                "products": [],
                "fills": [],
                "early_refills": 0,
                "total_quantity": 0,
            }

        p = patient_patterns[pid]
        p["prescribers"].add(sl.extra_attributes.get("prescriber_id"))
        p["products"].append(product.id)
        p["fills"].append({
            "date": sale.sale_timestamp.date(),
            "product_id": product.id,
            "product_name": product.canonical_name,
            "quantity": float(sl.quantity),
            "days_supply": sl.extra_attributes.get("days_supply", 30),
            "prescriber_id": sl.extra_attributes.get("prescriber_id"),
        })
        p["total_quantity"] += float(sl.quantity)

    # Detect red flags
    alerts = []
    for pid, p in patient_patterns.items():
        # Multiple prescribers (doctor shopping signal)
        if len(p["prescribers"]) > 3:
            alerts.append({
                "type": "multiple_prescribers",
                "severity": "high",
                "patient_id": pid,
                "patient_hash": p["patient_hash"],
                "prescriber_count": len(p["prescribers"]),
                "message": f"Patient has controlled substance prescriptions from {len(p['prescribers'])} different prescribers",
                "recommended_action": "Review PDMP, verify medical necessity",
            })

        # Early refills
        fills_by_product = {}
        for fill in p["fills"]:
            if fill["product_id"] not in fills_by_product:
                fills_by_product[fill["product_id"]] = []
            fills_by_product[fill["product_id"]].append(fill)

        for prod_id, fills in fills_by_product.items():
            fills.sort(key=lambda x: x["date"])
            for i in range(1, len(fills)):
                prev = fills[i-1]
                curr = fills[i]
                expected_date = prev["date"] + timedelta(days=prev["days_supply"])
                if curr["date"] < expected_date - timedelta(days=2):  # More than 2 days early
                    p["early_refills"] += 1
                    alerts.append({
                        "type": "early_refill",
                        "severity": "medium",
                        "patient_id": pid,
                        "patient_hash": p["patient_hash"],
                        "product_id": prod_id,
                        "days_early": (expected_date - curr["date"]).days,
                        "message": f"Early refill detected: {curr['product_name']} filled {(expected_date - curr['date']).days} days early",
                        "recommended_action": "Verify clinical need, check PDMP",
                    })

        # High quantity
        if p["total_quantity"] > 500:  # Threshold configurable
            alerts.append({
                "type": "high_quantity",
                "severity": "medium",
                "patient_id": pid,
                "patient_hash": p["patient_hash"],
                "total_quantity": p["total_quantity"],
                "message": f"High controlled substance quantity dispensed: {p['total_quantity']} units in 90 days",
                "recommended_action": "Review for medical necessity",
            })

    return {
        "period_days": 90,
        "controlled_products_count": len(controlled_products),
        "patients_with_controlled": len(patient_patterns),
        "alerts": alerts,
        "alert_summary": {
            "high": len([a for a in alerts if a["severity"] == "high"]),
            "medium": len([a for a in alerts if a["severity"] == "medium"]),
            "low": len([a for a in alerts if a["severity"] == "low"]),
        },
    }


def procurement_supplier_analytics(db, association_id, start_date=None, end_date=None):
    """Procurement and supplier performance analytics."""
    from datetime import date, timedelta

    if not end_date:
        end_date = date.today()
    if not start_date:
        start_date = end_date - timedelta(days=90)

    # Purchase order analysis
    po_q = db.query(PurchaseOrders).filter(
        PurchaseOrders.association_id == association_id,
        PurchaseOrders.order_date >= start_date,
        PurchaseOrders.order_date <= end_date,
    ).all()

    supplier_performance = {}
    for po in po_q:
        sid = po.supplier_id
        if sid not in supplier_performance:
            supplier = db.query(Suppliers).filter(Suppliers.id == sid).first()
            supplier_performance[sid] = {
                "supplier_id": sid,
                "supplier_name": supplier.supplier_name if supplier else "Unknown",
                "is_340b": supplier.is_340b if supplier else False,
                "lead_time_days": supplier.lead_time_days if supplier else None,
                "contract_fill_rate": supplier.fill_rate if supplier else None,
                "total_pos": 0,
                "total_ordered_value": 0,
                "total_received_value": 0,
                "on_time_deliveries": 0,
                "late_deliveries": 0,
                "fill_rates": [],
                "cost_variance": 0,
                "contract_vs_non_contract": {"contract": 0, "non_contract": 0},
            }

        sp = supplier_performance[sid]
        sp["total_pos"] += 1
        ordered_val = float(po.ordered_quantity) * float(po.unit_cost)
        received_val = float(po.received_quantity) * float(po.unit_cost)
        sp["total_ordered_value"] += ordered_val
        sp["total_received_value"] += received_val

        # Fill rate for this PO
        if po.ordered_quantity > 0:
            fill_rate = float(po.received_quantity) / float(po.ordered_quantity) * 100
            sp["fill_rates"].append(fill_rate)

        # On-time delivery
        if po.actual_delivery_date and po.expected_delivery_date:
            if po.actual_delivery_date <= po.expected_delivery_date:
                sp["on_time_deliveries"] += 1
            else:
                sp["late_deliveries"] += 1
                days_late = (po.actual_delivery_date - po.expected_delivery_date).days
                sp.setdefault("late_days", []).append(days_late)

        # Cost variance (vs product standard cost)
        product = db.query(Products).filter(Products.id == po.product_id).first()
        if product and product.cost_per_unit:
            std_cost = float(product.cost_per_unit)
            actual_cost = float(po.unit_cost)
            variance = (actual_cost - std_cost) * float(po.received_quantity)
            sp["cost_variance"] += variance

    # Calculate summary metrics
    for sp in supplier_performance.values():
        sp["avg_fill_rate"] = sum(sp["fill_rates"]) / len(sp["fill_rates"]) if sp["fill_rates"] else 0
        sp["on_time_rate"] = (sp["on_time_deliveries"] / sp["total_pos"] * 100) if sp["total_pos"] > 0 else 0
        sp["avg_late_days"] = sum(sp.get("late_days", [0])) / max(len(sp.get("late_days", [1])), 1)

    # 340B opportunity analysis
    products_340b_eligible = db.query(Products).filter(
        Products.association_id == association_id,
        # Would need a 340b_eligible flag on products
    ).all()

    return {
        "period": {"start": str(start_date), "end": str(end_date)},
        "supplier_performance": list(supplier_performance.values()),
        "summary": {
            "total_suppliers": len(supplier_performance),
            "total_po_value": sum(sp["total_ordered_value"] for sp in supplier_performance.values()),
            "total_received_value": sum(sp["total_received_value"] for sp in supplier_performance.values()),
            "overall_fill_rate": sum(sp["avg_fill_rate"] for sp in supplier_performance.values()) / max(len(supplier_performance), 1),
            "overall_on_time_rate": sum(sp["on_time_rate"] for sp in supplier_performance.values()) / max(len(supplier_performance), 1),
        },
        "recommendations": [
            {
                "type": "supplier_review",
                "supplier_id": sp["supplier_id"],
                "supplier_name": sp["supplier_name"],
                "issue": "Low fill rate" if sp["avg_fill_rate"] < 90 else "Late deliveries" if sp["on_time_rate"] < 90 else "Cost variance",
                "recommendation": "Negotiate terms or find alternative" if sp["avg_fill_rate"] < 90 else "Discuss delivery schedule" if sp["on_time_rate"] < 90 else "Review pricing",
            }
            for sp in supplier_performance.values()
            if sp["avg_fill_rate"] < 90 or sp["on_time_rate"] < 90 or sp["cost_variance"] > 1000
        ],
    }