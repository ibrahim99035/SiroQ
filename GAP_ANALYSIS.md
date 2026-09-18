# SiroQ vs. Pharmacy Analytics Specification — Gap Analysis

**Generated:** 2026-09-18  
**Source Spec:** User-provided detailed pharmacy analytics business logic  
**Current Codebase:** SiroQ (FastAPI + SQLAlchemy + PostgreSQL RLS)

---

## Executive Summary

| Category | Spec Coverage | Status |
|----------|---------------|--------|
| **Core Domain Entities** | 6/8 entities modeled | ⚠️ Partial |
| **Analytical Modules** | 2/6 modules implemented | ❌ Major gaps |
| **Calculation Layers** | Basic aggregation only | ❌ Missing |
| **Data Flow & Governance** | Bronze→Silver + RLS | ✅ Strong |
| **Decision Support** | Basic dashboards | ⚠️ Partial |
| **Design Principles** | Auditability, configurability | ⚠️ Partial |

**Overall:** SiroQ has a solid **data ingestion + multi-tenant foundation** but implements only ~25% of the specified pharmacy analytics business logic.

---

## 1. Core Domain Entities — Coverage

| Spec Entity | SiroQ Model | Status | Missing Fields |
|-------------|-------------|--------|----------------|
| **Product / SKU** | `Products` | ⚠️ Partial | NDC, controlled schedule, cost, selling price, reimbursement rates, shelf life, storage reqs, therapeutic class |
| **Inventory Lot / Batch** | `Batches` | ⚠️ Partial | Quantity on hand (computed), location (shelf/fridge/narcotics), cost basis |
| **Transaction / Dispense** | `Sales` + `SaleLines` | ⚠️ Partial | Prescription vs OTC, days' supply, refill number, patient (anonymized), payer/insurance, copay, acquisition cost, margin |
| **Prescription / Order** | — | ❌ Missing | Original Rx, refills remaining, adherence signals, prior auth status |
| **Supplier / Purchase Order** | `InventoryEvents` (supplier field only) | ❌ Missing | Cost, lead time, fill rate, contract pricing, PO entity |
| **Patient / Customer** | — | ❌ Missing | Demographics, loyalty, chronic conditions, adherence cohort |
| **Payer / Reimbursement** | — | ❌ Missing | Plan, expected vs actual, rejection reasons, DIR fees, clawbacks |
| **Store / Location / Pharmacist** | `Pharmacies` + `Users` | ✅ Covered | Pharmacist linkage via Users |

---

## 2. Analytical Modules — Implementation Status

### 2.1 Inventory Optimization & Risk ❌ **NOT IMPLEMENTED**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Days-of-supply, turnover rate | ❌ Only basic `units_near_expiry` count |
| Dead stock (no movement > X days) | ❌ |
| Near-expiry risk (30/60/90-day value at risk) | ❌ Only count, no value |
| Reorder-point / safety-stock calculation | ❌ |
| ABC/XYZ classification | ❌ |
| Controlled-substance perpetual inventory reconciliation | ❌ |
| Substitution / therapeutic interchange scoring | ❌ |

**Current:** `inventory_kpis()` returns only: `units_near_expiry` (count), `waste_cost`, `turnover` (naive)

### 2.2 Sales, Margin & Revenue Integrity ⚠️ **PARTIAL**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Gross / contribution / net margin | ❌ Only `total_revenue`, `avg_basket` |
| Payer mix analysis | ❌ No payer field in Sales |
| Price elasticity / promotional lift | ❌ |
| Cash vs third-party mix | ❌ |
| Script volume vs revenue, new vs refill | ⚠️ Basic volume only |
| Specialty vs retail split | ❌ |

**Current:** `sales_kpis()` returns only: `total_revenue`, `transactions`, `avg_basket`, daily revenue series

### 2.3 Prescription & Adherence Analytics ❌ **NOT IMPLEMENTED**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| PDC (Proportion of Days Covered) | ❌ |
| MPR (Medication Possession Ratio) | ❌ |
| Refill gap detection | ❌ |
| High-risk non-adherence cohorts | ❌ |
| Therapy initiation / persistence / switching | ❌ |
| Controlled-substance utilization patterns | ❌ |

### 2.4 Procurement & Supplier Performance ❌ **NOT IMPLEMENTED**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Cost-per-unit trends, contract compliance | ❌ |
| Fill-rate, on-time delivery | ❌ |
| Shortage impact scoring | ❌ |
| Make-vs-buy / 340B opportunity | ❌ |

### 2.5 Operational & Workforce ❌ **NOT IMPLEMENTED**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Dispense cycle time, queue depth | ❌ |
| Pharmacist vs technician workload | ❌ |
| Error / near-miss rates | ❌ |
| Peak-hour staffing recommendations | ❌ |

### 2.6 Compliance, Audit & Risk ⚠️ **PARTIAL**
| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Controlled-substance variance thresholds | ❌ |
| Expiration / recall cascade (lot-level quarantine) | ❌ |
| Audit-trail completeness | ✅ `EditAuditLog` covers all edits |
| Access logging | ⚠️ Only via RLS + audit log |
| Regulatory reporting extracts | ❌ |

---

## 3. Calculation & Decision Layers

| Spec Layer | SiroQ Status | Notes |
|------------|--------------|-------|
| **Fact aggregation layer** (daily/weekly snapshots) | ❌ | Only on-the-fly queries in `analytics.py` |
| **Rule engine** (configurable thresholds → alerts) | ❌ | No alerting system |
| **Forecasting** (demand, seasonality, promo lift) | ❌ | None |
| **Optimization** (inventory positioning, interchange ranking) | ❌ | None |
| **Segmentation** (patient cohorts, product portfolios) | ❌ | None |
| **Versioned calculation definitions** | ❌ | Hardcoded in Python functions |

---

## 4. Data Flow & Governance

| Spec Requirement | SiroQ Status |
|------------------|--------------|
| Ingest from POS, PMS, wholesaler EDI, claims, EHR | ⚠️ CSV/Excel/ZIP upload only |
| Canonical product key (NDC + lot) | ⚠️ `Products.raw_name` + `Batches.lot_number` — no NDC |
| De-identified patient ID | ❌ No patient entity |
| Privacy rules (HIPAA/GDPR) at logic boundary | ⚠️ RLS provides tenant isolation, no patient PII handling |
| Role-based views, minimum necessary data | ✅ RLS + RBAC |
| Audit logging of identifiable record access | ✅ `EditAuditLog` for edits |
| Versioned calculations | ❌ |

---

## 5. User-Facing Decision Support

| Spec Feature | SiroQ Status |
|--------------|--------------|
| Role-specific views (pharmacist, inventory mgr, finance, ops) | ⚠️ Same dashboards for all roles |
| Recommended actions on alerts | ❌ No alerts |
| What-if scenarios | ❌ |
| Explainability (show inputs/rules behind scores) | ❌ |

---

## 6. Design Principles Compliance

| Principle | SiroQ Status |
|-----------|--------------|
| Actionability over pure reporting | ⚠️ Dashboards show metrics, no actions |
| Lot- and claim-level granularity | ✅ Lot-level via `Batches` + `InventoryEvents` |
| Explainability | ❌ |
| Configurability (thresholds per org) | ❌ Hardcoded |
| Auditability (reproducible results) | ✅ Strong — audit log + immutable bronze |

---

## 7. Specific Model Gaps — Fields to Add

### `Products` — Add:
```python
ndc: Mapped[str | None] = mapped_column(Text(11), nullable=True, unique=True)  # National Drug Code
generic_name: Mapped[str | None] = mapped_column(nullable=True)
brand_name: Mapped[str | None] = mapped_column(nullable=True)
therapeutic_class: Mapped[str | None] = mapped_column(nullable=True)  # e.g., ATC code
controlled_schedule: Mapped[str | None] = mapped_column(nullable=True)  # C-II, C-III, etc.
cost_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
selling_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
reimbursement_rate: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
shelf_life_days: Mapped[int | None] = mapped_column(nullable=True)
storage_requirements: Mapped[str | None] = mapped_column(nullable=True)  # fridge, controlled, etc.
```

### `Batches` — Add:
```python
quantity_on_hand: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
location: Mapped[str | None] = mapped_column(nullable=True)  # shelf, fridge, narcotics_cabinet
cost_basis: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
```

### `Sales` — Add:
```python
is_prescription: Mapped[bool] = mapped_column(default=True)
days_supply: Mapped[int | None] = mapped_column(nullable=True)
refill_number: Mapped[int | None] = mapped_column(default=0)
patient_hash: Mapped[str | None] = mapped_column(Text(64), nullable=True)  # anonymized
payer_id: Mapped[str | None] = mapped_column(Text(64), nullable=True)
copay_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
acquisition_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
margin: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)  # computed
```

### New Models Needed:
- `Prescriptions` — links to patient, prescriber, product, refills, prior_auth
- `Patients` (anonymized) — demographics, chronic conditions, adherence cohort
- `Payers` — plan, expected payment, rejection codes, DIR fees
- `Suppliers` / `PurchaseOrders` — contract pricing, lead time, fill rate
- `Alerts` / `Rules` — configurable thresholds, actions

---

## 8. Recommended Implementation Priority

| Priority | Module | Effort | Dependencies |
|----------|--------|--------|--------------|
| **P0** | Enhanced `Products` + `Batches` fields | 1 week | Migration |
| **P0** | `Prescriptions` + `Patients` (anonymized) model | 1 week | Migration |
| **P1** | Inventory KPIs: days-of-supply, turnover, dead stock, expiry value-at-risk | 1 week | Batch qty_on_hand |
| **P1** | Sales KPIs: margin, payer mix, cash vs 3rd-party | 1 week | Sales new fields |
| **P1** | Reorder point / safety stock calculation | 1 week | Demand forecast |
| **P2** | Adherence analytics (PDC, MPR, refill gaps) | 2 weeks | Prescriptions + patient |
| **P2** | Controlled substance reconciliation | 1 week | Batch tracking + alerts |
| **P2** | Rule engine + alerting framework | 2 weeks | Configurable thresholds |
| **P3** | Demand forecasting (statistical) | 2 weeks | Historical data |
| **P3** | Therapeutic interchange scoring | 1 week | Product therapeutic class |
| **P3** | What-if scenario engine | 2 weeks | Calculation versioning |

---

## 9. Immediate Action Items

1. **Extend `Products` model** with NDC, therapeutic class, controlled schedule, pricing fields
2. **Add `quantity_on_hand` to `Batches`** (computed from `InventoryEvents` or stored)
3. **Add `Prescriptions` and `Patients` models** for adherence analytics
4. **Enhance `Sales`/`SaleLines`** with prescription flag, days_supply, payer, margin
5. **Build fact aggregation layer** — materialized daily snapshots for fast dashboards
6. **Implement rule engine** — configurable thresholds → alerts with recommended actions
7. **Add role-specific dashboard views** — pharmacist vs inventory manager vs finance

---

## Conclusion

SiroQ provides an **excellent foundation**:
- ✅ Multi-tenant RLS architecture
- ✅ Immutable bronze + normalized silver
- ✅ Human-in-the-loop ingestion with validation
- ✅ Full audit trail
- ✅ Clean API + UI structure

**But the pharmacy analytics business logic is only ~25% implemented.** The spec describes a comprehensive clinical/financial/operational decision platform; SiroQ currently delivers basic sales + inventory dashboards on top of a solid data pipeline.

**Recommendation:** Treat the current codebase as **Phase 1 (Data Platform)** and plan **Phase 2 (Analytics Engine)** using the gap matrix above.