"""Analytics KPI tests: currency stamping, real semantics, and scope filtering.

Runs against the live database as the least-privilege ``siroq_app`` role with
Row-Level-Security in force. Each test builds a fresh isolated association so the
assertions can be exact and never depend on demo data.
"""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from app.config import settings
from app.database import SessionLocal
from app.models.models import (
    Applications,
    Associations,
    Batches,
    InventoryEvents,
    Pharmacies,
    Products,
    SaleLines,
    Sales,
)
from app.services.analytics import inventory_kpis, sales_kpis


def _ctx(db, assoc_id):
    db.execute(
        text("SELECT set_config('app.current_association_id', :a, true)"),
        {"a": str(assoc_id)},
    )


def _purge(assoc_id):
    """Remove a test tenant using the RLS-bypassing owner role."""
    owner = create_engine(settings.MIGRATIONS_DATABASE_URL, future=True)
    with owner.begin() as conn:
        for table in ("sale_lines", "inventory_events", "sales", "batches",
                      "products", "applications", "pharmacies"):
            conn.execute(
                text(f"DELETE FROM {table} WHERE association_id = CAST(:a AS uuid)"),
                {"a": assoc_id},
            )
        conn.execute(
            text("DELETE FROM associations WHERE id = CAST(:a AS uuid)"),
            {"a": assoc_id},
        )
    owner.dispose()


@pytest.fixture
def tenant():
    """Fresh tenant: 2 pharmacies, 1 product, 1 batch, 2 sales, 1 write-off.

    Pharmacy A -> revenue 100, write-off 5 units @ 2.0
    Pharmacy B -> revenue 50, no inventory movement
    """
    assoc_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        _ctx(db, assoc_id)
        db.add(Associations(id=assoc_id, name="KPI Tenant", default_currency="EGP"))
        db.flush()

        ph_a = Pharmacies(id=str(uuid.uuid4()), association_id=assoc_id, name="KPI A")
        ph_b = Pharmacies(id=str(uuid.uuid4()), association_id=assoc_id, name="KPI B")
        app = Applications(id=str(uuid.uuid4()), association_id=assoc_id,
                           name="KPI App", source_type="manual_upload", status="active")
        product = Products(id=str(uuid.uuid4()), association_id=assoc_id,
                           raw_name="Para", canonical_name="Paracetamol",
                           cost_per_unit=2.0)
        db.add_all([ph_a, ph_b, app, product])
        db.flush()

        batch = Batches(id=str(uuid.uuid4()), association_id=assoc_id,
                        product_id=product.id, lot_number="LOT-KPI",
                        expiry_date=date.today() + timedelta(days=10),
                        quantity_on_hand=100, cost_basis=2.0)
        db.add(batch)
        db.flush()

        for ph, amount, qty in ((ph_a, 100.0, 10.0), (ph_b, 50.0, 5.0)):
            sale = Sales(id=str(uuid.uuid4()), association_id=assoc_id,
                         pharmacy_id=ph.id, application_id=app.id,
                         sale_timestamp=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
                         total_amount=amount, currency="EGP")
            db.add(sale)
            db.flush()
            db.add(SaleLines(id=str(uuid.uuid4()), association_id=assoc_id,
                             sale_id=sale.id, product_id=product.id,
                             quantity=qty, unit_price=10.0))

        db.add(InventoryEvents(
            id=str(uuid.uuid4()), association_id=assoc_id,
            pharmacy_id=ph_a.id, application_id=app.id, batch_id=batch.id,
            event_type="expiry_writeoff", quantity=5, unit_cost=2.0,
            event_timestamp=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
        ))
        # Capture ids before commit: commit expires ORM attributes, and reloading
        # them afterwards would re-enter the RLS-gated SELECT.
        ids = {"assoc": assoc_id, "ph_a": ph_a.id, "ph_b": ph_b.id,
               "product": product.id, "batch": batch.id, "app": app.id}
        db.commit()

        yield ids
    finally:
        db.close()
        _purge(assoc_id)


def test_sales_kpis_revenue_and_currency(tenant):
    db = SessionLocal()
    try:
        _ctx(db, tenant["assoc"])
        k = sales_kpis(db, tenant["assoc"])
        assert k["currency"] == "EGP"
        assert k["total_revenue"] == 150.0
        assert k["transactions"] == 2
        assert k["avg_basket"] == 75.0
        assert k["revenues"] == [150.0]
    finally:
        db.close()


def test_sales_kpis_series_honours_pharmacy_scope(tenant):
    """Regression: the daily series used to ignore the pharmacy filter entirely."""
    db = SessionLocal()
    try:
        _ctx(db, tenant["assoc"])
        k = sales_kpis(db, tenant["assoc"], pharmacy_id=tenant["ph_a"])
        assert k["total_revenue"] == 100.0
        assert k["transactions"] == 1
        # the chart data must reflect the same scope as the tiles
        assert k["revenues"] == [100.0]
    finally:
        db.close()


def test_transactions_count_distinct_receipts(tenant):
    """Regression: a receipt whose lines arrive as separate rows must count as
    ONE transaction, not one per row."""
    db = SessionLocal()
    try:
        _ctx(db, tenant["assoc"])
        # two rows that belong to the same receipt R-1
        for amount in (10.0, 5.0):
            db.add(Sales(id=str(uuid.uuid4()), association_id=tenant["assoc"],
                         pharmacy_id=tenant["ph_a"], application_id=tenant["app"],
                         sale_timestamp=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
                         total_amount=amount, currency="EGP", transaction_ref="R-1"))
        db.commit()

        # The tenant context is transaction-local, so a commit ends it; re-set it
        # exactly as the per-request dependency does in the running app.
        _ctx(db, tenant["assoc"])

        k = sales_kpis(db, tenant["assoc"])
        # ph_a standalone sale + receipt R-1 + ph_b sale
        assert k["transactions"] == 3
        assert k["total_revenue"] == 165.0
        assert k["avg_basket"] == pytest.approx(55.0)
    finally:
        db.close()


def test_inventory_kpis_are_real_values_and_scoped(tenant):
    db = SessionLocal()
    try:
        _ctx(db, tenant["assoc"])
        k = inventory_kpis(db, tenant["assoc"])
        # units on hand near expiry, not a count of batch rows
        assert k["units_near_expiry"] == 100.0
        # valued waste: 5 units x 2.0 (was previously a bare unit sum)
        assert k["waste_cost"] == 10.0
        # turnover = revenue / current stock value (100 x 2.0)
        assert k["turnover"] == pytest.approx(0.75)
        assert k["currency"] == "EGP"

        scoped = inventory_kpis(db, tenant["assoc"], pharmacy_id=tenant["ph_b"])
        # pharmacy B has no linked batches or write-offs
        assert scoped["units_near_expiry"] == 0.0
        assert scoped["waste_cost"] == 0.0
    finally:
        db.close()
