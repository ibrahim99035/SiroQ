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
    func,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Associations(Base):
    __tablename__ = "associations"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
    )
    name: Mapped[str] = mapped_column(nullable=False)
    country: Mapped[str | None] = mapped_column(nullable=True)
    default_currency: Mapped[str] = mapped_column(
        Text(3), nullable=False, default="USD"
    )
    default_timezone: Mapped[str] = mapped_column(
        Text(32), nullable=False, default="UTC"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    pharmacies = relationship("Pharmacies", back_populates="association")
    users = relationship("Users", back_populates="association")
    products = relationship("Products", back_populates="association")
    datasets = relationship("Datasets", back_populates="association")


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
    role: Mapped[str] = mapped_column(nullable=False)
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


class MappingProfiles(Base):
    __tablename__ = "mapping_profiles"

    id: Mapped[str] = mapped_column(
        Uuid(), primary_key=True, default=func.gen_random_uuid()
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
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    association = relationship("Associations", back_populates="products")
    sale_lines = relationship("SaleLines", back_populates="product")


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