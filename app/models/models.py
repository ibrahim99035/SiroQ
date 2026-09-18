from datetime import datetime
from typing import Dict, Any

from app.database import Base

from sqlalchemy import (
    Uuid,
    Text,
    Numeric,
    Boolean,
    ForeignKey,
    Integer,
    CheckConstraint,
    Enum,
    func,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

user_role_enum = Enum(
    "association_admin", "pharmacy_manager", "analyst", "data_steward", "viewer",
    name="user_role_enum", create_type=False,
)


class Associations(Base):
    __tablename__ = "associations"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(nullable=False)
    country: Mapped[str | None] = mapped_column(nullable=True)
    default_currency: Mapped[str] = mapped_column(
        Text(3), nullable=False, default="EGP"
    )
    default_timezone: Mapped[str] = mapped_column(
        Text(32), nullable=False, default="Africa/Cairo"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    pharmacies = relationship("Pharmacies", back_populates="association")
    users = relationship("Users", back_populates="association")
    products = relationship("Products", back_populates="association")
    datasets = relationship("Datasets", back_populates="association")
    applications = relationship("Applications", back_populates="association")
    batches = relationship("Batches", back_populates="association")
    sales = relationship("Sales", back_populates="association")
    sale_lines = relationship("SaleLines", back_populates="association")
    inventory_events = relationship("InventoryEvents", back_populates="association")
    prescribers = relationship("Prescribers", back_populates="association")
    patients = relationship("Patients", back_populates="association")
    prescriptions = relationship("Prescriptions", back_populates="association")
    payers = relationship("Payers", back_populates="association")
    suppliers = relationship("Suppliers", back_populates="association")
    purchase_orders = relationship("PurchaseOrders", back_populates="association")
    alert_rules = relationship("AlertRules", back_populates="association")
    alerts = relationship("Alerts", back_populates="association")
    edit_audit_log = relationship("EditAuditLog", back_populates="association")


class Pharmacies(Base):
    __tablename__ = "pharmacies"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    external_code: Mapped[str | None] = mapped_column(nullable=True)
    address_raw: Mapped[str | None] = mapped_column(nullable=True)
    lat: Mapped[float | None] = mapped_column(nullable=True)
    lng: Mapped[float | None] = mapped_column(nullable=True)
    region: Mapped[str | None] = mapped_column(nullable=True)
    timezone: Mapped[str | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="pharmacies")
    users = relationship("Users", back_populates="pharmacy")
    applications = relationship("Applications", back_populates="pharmacy")
    inventory_events = relationship(
        "InventoryEvents", back_populates="pharmacy"
    )
    sales = relationship("Sales", back_populates="pharmacy")


class Users(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str | None] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=True
    )
    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(nullable=False)
    full_name: Mapped[str] = mapped_column(nullable=False)
    role: Mapped[str] = mapped_column(user_role_enum, nullable=False)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="users")
    pharmacy = relationship("Pharmacies", back_populates="users")
    edit_audit_log = relationship(
        "EditAuditLog", back_populates="user"
    )


class Applications(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str | None] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(nullable=False)
    source_type: Mapped[str] = mapped_column(
        Text(20), nullable=False, default="manual_upload"
    )
    pharmacy_identifier_column: Mapped[str | None] = mapped_column(
        Text(64), nullable=True
    )
    status: Mapped[str] = mapped_column(
        Text(20), nullable=False, default="active"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="applications")
    pharmacy = relationship("Pharmacies", back_populates="applications")
    mapping_profile = relationship(
        "MappingProfiles", back_populates="application", uselist=False
    )
    datasets = relationship("Datasets", back_populates="application")
    sales = relationship("Sales", back_populates="application")
    inventory_events = relationship("InventoryEvents", back_populates="application")


class MappingProfiles(Base):
    __tablename__ = "mapping_profiles"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), unique=True, nullable=False
    )
    field_map: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=lambda: {}
    )
    confidence_map: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=lambda: {}
    )
    confirmed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    application = relationship("Applications", back_populates="mapping_profile")


class Datasets(Base):
    __tablename__ = "datasets"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), nullable=False
    )
    bronze_file_path: Mapped[str] = mapped_column(nullable=False)
    original_filename: Mapped[str] = mapped_column(nullable=False)
    uploaded_by: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    status: Mapped[str] = mapped_column(
        Text(20), nullable=False, default="pending_mapping"
    )
    row_count: Mapped[int | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)

    association = relationship("Associations", back_populates="datasets")
    application = relationship("Applications", back_populates="datasets")


class Products(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    raw_name: Mapped[str] = mapped_column(nullable=False)
    canonical_name: Mapped[str | None] = mapped_column(nullable=True)
    form: Mapped[str | None] = mapped_column(nullable=True)
    strength: Mapped[str | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(nullable=True)
    category: Mapped[str | None] = mapped_column(nullable=True)
    ndc: Mapped[str | None] = mapped_column(Text(11), nullable=True, unique=True)
    generic_name: Mapped[str | None] = mapped_column(nullable=True)
    brand_name: Mapped[str | None] = mapped_column(nullable=True)
    therapeutic_class: Mapped[str | None] = mapped_column(nullable=True)
    controlled_schedule: Mapped[str | None] = mapped_column(nullable=True)
    cost_per_unit: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    selling_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    reimbursement_rate: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    shelf_life_days: Mapped[int | None] = mapped_column(nullable=True)
    storage_requirements: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="products")
    batches = relationship("Batches", back_populates="product")
    sale_lines = relationship("SaleLines", back_populates="product")
    prescriptions = relationship("Prescriptions", back_populates="product")
    purchase_orders = relationship("PurchaseOrders", back_populates="product")


class Batches(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    lot_number: Mapped[str] = mapped_column(nullable=False)
    expiry_date: Mapped[datetime | None] = mapped_column(nullable=True)
    received_date: Mapped[datetime | None] = mapped_column(nullable=True)
    supplier: Mapped[str | None] = mapped_column(nullable=True)
    quantity_on_hand: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    location: Mapped[str | None] = mapped_column(nullable=True)
    cost_basis: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="batches")
    product = relationship("Products", back_populates="batches")
    inventory_events = relationship(
        "InventoryEvents", back_populates="batch"
    )


class InventoryEvents(Base):
    __tablename__ = "inventory_events"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("batches.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(
        Text(20), nullable=False
    )
    quantity: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False
    )
    unit_cost: Mapped[float | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    event_timestamp: Mapped[datetime] = mapped_column(
        nullable=False
    )
    extra_attributes: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=lambda: {}
    )

    association = relationship("Associations", back_populates="inventory_events")
    pharmacy = relationship("Pharmacies", back_populates="inventory_events")
    application = relationship("Applications", back_populates="inventory_events")
    batch = relationship("Batches", back_populates="inventory_events")


class Sales(Base):
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False
    )
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id"), nullable=False
    )
    sale_timestamp: Mapped[datetime] = mapped_column(
        nullable=False
    )
    payment_method: Mapped[str | None] = mapped_column(
        Text(32), nullable=True
    )
    total_amount: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False
    )
    currency: Mapped[str] = mapped_column(Text(3), nullable=False, default="USD")
    extra_attributes: Mapped[Dict[str, Any]] = mapped_column(
        JSON, default=lambda: {}
    )

    association = relationship("Associations", back_populates="sales")
    pharmacy = relationship("Pharmacies", back_populates="sales")
    application = relationship("Applications", back_populates="sales")
    sale_lines = relationship("SaleLines", back_populates="sale")


class SaleLines(Base):
    __tablename__ = "sale_lines"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    sale_id: Mapped[str] = mapped_column(
        ForeignKey("sales.id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    prescriber_id: Mapped[str | None] = mapped_column(
        ForeignKey("prescribers.id"), nullable=True
    )
    quantity: Mapped[float] = mapped_column(
        Numeric(12, 3), nullable=False
    )
    unit_price: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False
    )

    association = relationship("Associations", back_populates="sale_lines")
    sale = relationship("Sales", back_populates="sale_lines")
    product = relationship("Products", back_populates="sale_lines")
    prescriber = relationship("Prescribers", back_populates="sale_lines")


class Prescribers(Base):
    __tablename__ = "prescribers"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    raw_name: Mapped[str] = mapped_column(nullable=False)
    specialty: Mapped[str | None] = mapped_column(nullable=True)
    license_id: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="prescribers")
    sale_lines = relationship("SaleLines", back_populates="prescriber")
    prescriptions = relationship("Prescriptions", back_populates="prescriber")


class Patients(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    patient_hash: Mapped[str] = mapped_column(Text(64), nullable=False, unique=True)
    age_bracket: Mapped[str | None] = mapped_column(nullable=True)
    gender: Mapped[str | None] = mapped_column(Text(1), nullable=True)
    chronic_conditions: Mapped[list[str]] = mapped_column(
        JSON, default=lambda: []
    )
    adherence_cohort: Mapped[str | None] = mapped_column(nullable=True)
    loyalty_segment: Mapped[str | None] = mapped_column(nullable=True)
    first_seen_date: Mapped[datetime | None] = mapped_column(nullable=True)
    last_seen_date: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="patients")
    prescriptions = relationship("Prescriptions", back_populates="patient")


class Prescriptions(Base):
    __tablename__ = "prescriptions"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    patient_id: Mapped[str] = mapped_column(
        ForeignKey("patients.id"), nullable=False
    )
    prescriber_id: Mapped[str | None] = mapped_column(
        ForeignKey("prescribers.id"), nullable=True
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    rx_number: Mapped[str] = mapped_column(nullable=False)
    original_rx_date: Mapped[datetime] = mapped_column(nullable=False)
    days_supply: Mapped[int] = mapped_column(nullable=False)
    refills_authorized: Mapped[int] = mapped_column(default=0)
    refills_remaining: Mapped[int] = mapped_column(default=0)
    prior_auth_status: Mapped[str | None] = mapped_column(nullable=True)
    prior_auth_number: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="prescriptions")
    patient = relationship("Patients", back_populates="prescriptions")
    prescriber = relationship("Prescribers", back_populates="prescriptions")
    product = relationship("Products", back_populates="prescriptions")


class Payers(Base):
    __tablename__ = "payers"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    payer_name: Mapped[str] = mapped_column(nullable=False)
    plan_name: Mapped[str | None] = mapped_column(nullable=True)
    payer_type: Mapped[str | None] = mapped_column(nullable=True)
    contract_rate: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    dir_fee_rate: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="payers")


class Suppliers(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    supplier_name: Mapped[str] = mapped_column(nullable=False)
    contact_email: Mapped[str | None] = mapped_column(nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(nullable=True)
    fill_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    is_340b: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="suppliers")
    purchase_orders = relationship("PurchaseOrders", back_populates="supplier")


class PurchaseOrders(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.id"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(nullable=False)
    ordered_quantity: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False)
    received_quantity: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    order_date: Mapped[datetime] = mapped_column(nullable=False)
    expected_delivery_date: Mapped[datetime | None] = mapped_column(nullable=True)
    actual_delivery_date: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(Text(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="purchase_orders")
    supplier = relationship("Suppliers", back_populates="purchase_orders")
    product = relationship("Products", back_populates="purchase_orders")


class AlertRules(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    module: Mapped[str] = mapped_column(Text(30), nullable=False)
    metric_name: Mapped[str] = mapped_column(nullable=False)
    condition: Mapped[str] = mapped_column(Text(20), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    severity: Mapped[str] = mapped_column(Text(20), nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(nullable=True)

    association = relationship("Associations", back_populates="alert_rules")
    created_by_user = relationship("Users", foreign_keys=[created_by])
    alerts = relationship("Alerts", back_populates="rule")


class Alerts(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("alert_rules.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(Text(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(nullable=False)
    metric_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    severity: Mapped[str] = mapped_column(Text(20), nullable=False)
    message: Mapped[str] = mapped_column(nullable=False)
    recommended_action: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(Text(20), nullable=False, default="open")
    acknowledged_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="alerts")
    rule = relationship("AlertRules", back_populates="alerts")
    acknowledged_by_user = relationship("Users", foreign_keys=[acknowledged_by])


class EditAuditLog(Base):
    __tablename__ = "edit_audit_log"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(nullable=False)
    entity_id: Mapped[str] = mapped_column(nullable=False)
    field: Mapped[str] = mapped_column(nullable=False)
    old_value: Mapped[str | None] = mapped_column(nullable=True)
    new_value: Mapped[str | None] = mapped_column(nullable=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    edited_at: Mapped[datetime] = mapped_column(server_default=func.now())
    reason: Mapped[str | None] = mapped_column(nullable=True)

    association = relationship("Associations", back_populates="edit_audit_log")
    user = relationship("Users", back_populates="edit_audit_log")


class DailySalesSnapshot(Base):
    __tablename__ = "daily_sales_snapshots"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False
    )
    snapshot_date: Mapped[datetime] = mapped_column(nullable=False)
    total_revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    transaction_count: Mapped[int] = mapped_column(default=0)
    total_units_sold: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    prescription_count: Mapped[int] = mapped_column(default=0)
    otc_count: Mapped[int] = mapped_column(default=0)
    cash_revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    insurance_revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    gross_margin: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    net_margin: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    new_patient_count: Mapped[int] = mapped_column(default=0)
    refill_count: Mapped[int] = mapped_column(default=0)
    unique_products_sold: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations")
    pharmacy = relationship("Pharmacies")

    __table_args__ = (
        UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", name="uq_daily_sales_snapshot"),
    )


class DailyInventorySnapshot(Base):
    __tablename__ = "daily_inventory_snapshots"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False
    )
    snapshot_date: Mapped[datetime] = mapped_column(nullable=False)
    total_inventory_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total_units_on_hand: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    unique_products_in_stock: Mapped[int] = mapped_column(default=0)
    units_near_expiry_30: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    value_near_expiry_30: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    units_near_expiry_60: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    value_near_expiry_60: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    units_near_expiry_90: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    value_near_expiry_90: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    dead_stock_units: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    dead_stock_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    waste_cost: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    turnover_rate: Mapped[float] = mapped_column(Numeric(10, 4), default=0)
    controlled_substance_variance: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations")
    pharmacy = relationship("Pharmacies")

    __table_args__ = (
        UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", name="uq_daily_inventory_snapshot"),
    )


class DailyAdherenceSnapshot(Base):
    __tablename__ = "daily_adherence_snapshots"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    pharmacy_id: Mapped[str] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False
    )
    snapshot_date: Mapped[datetime] = mapped_column(nullable=False)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    pdc_30: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    pdc_90: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    mpr_30: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    mpr_90: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    days_since_last_fill: Mapped[int | None] = mapped_column(nullable=True)
    refill_gap_days: Mapped[int | None] = mapped_column(nullable=True)
    is_adherent_80: Mapped[bool | None] = mapped_column(nullable=True)
    adherence_cohort: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations")
    pharmacy = relationship("Pharmacies")
    patient = relationship("Patients")
    product = relationship("Products")

    __table_args__ = (
        UniqueConstraint("association_id", "pharmacy_id", "snapshot_date", "patient_id", "product_id", name="uq_daily_adherence_snapshot"),
    )


class DailySupplierSnapshot(Base):
    __tablename__ = "daily_supplier_snapshots"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    association_id: Mapped[str] = mapped_column(
        ForeignKey("associations.id"), nullable=False
    )
    snapshot_date: Mapped[datetime] = mapped_column(nullable=False)
    supplier_id: Mapped[str] = mapped_column(ForeignKey("suppliers.id"), nullable=False)
    total_ordered_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total_received_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    fill_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    on_time_delivery_rate: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    avg_lead_time_days: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    open_po_count: Mapped[int] = mapped_column(default=0)
    open_po_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations")
    supplier = relationship("Suppliers")

    __table_args__ = (
        UniqueConstraint("association_id", "snapshot_date", "supplier_id", name="uq_daily_supplier_snapshot"),
    )