# SiroQ Schema Registry

This file must never drift from the live schema; update it in the same step as any migration, not after.

## Table: associations
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `name` TEXT NOT NULL
- `country` TEXT
- `default_currency` CHAR(3) NOT NULL DEFAULT 'USD'
- `default_timezone` TEXT NOT NULL DEFAULT 'UTC'
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

## Table: pharmacies
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `name` TEXT NOT NULL
- `external_code` TEXT
- `address_raw` TEXT
- `lat` NUMERIC(9,6) — Phase 1: geo lives directly on Pharmacy
- `lng` NUMERIC(9,6)
- `region` TEXT
- `timezone` TEXT — nullable; falls back to association.default_timezone
- `active` BOOLEAN NOT NULL DEFAULT true
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

## Table: users
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `pharmacy_id` UUID NULL FK -> pharmacies.id — NULL = association-wide scope
- `email` TEXT NOT NULL UNIQUE
- `hashed_password` TEXT NOT NULL
- `full_name` TEXT NOT NULL
- `role` user_role_enum NOT NULL — association_admin|pharmacy_manager|analyst|data_steward|viewer
- `active` BOOLEAN NOT NULL DEFAULT true
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

## Table: applications
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `pharmacy_id` UUID NULL FK -> pharmacies.id — NULL = multi-pharmacy source
- `name` TEXT NOT NULL
- `source_type` TEXT NOT NULL DEFAULT 'manual_upload'
- `pharmacy_identifier_column` TEXT NULL
- `status` TEXT NOT NULL DEFAULT 'active'
- `created_at` TIMESTAMPTZ NOT NULL DEFAULT now()

## Table: mapping_profiles
- `id` UUID PK
- `application_id` UUID NOT NULL UNIQUE FK -> applications.id
- `field_map` JSONB NOT NULL DEFAULT '{}' — {source_header: canonical_field}
- `confidence_map` JSONB NOT NULL DEFAULT '{}' — {canonical_field: 0-100}
- `confirmed_by` UUID NULL FK -> users.id
- `confirmed_at` TIMESTAMPTZ NULL

## Table: datasets
- `id` UUID PK
- `association_id` UUID NOT NULL FK -> associations.id
- `application_id` UUID NOT NULL FK -> applications.id
- `bronze_file_path` TEXT NOT NULL — path under BRONZE_STORAGE_PATH
- `original_filename` TEXT NOT NULL
- `uploaded_by` UUID NOT NULL FK -> users.id
- `uploaded_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `status` TEXT NOT NULL DEFAULT 'pending_mapping' — pending_mapping|needs_pharmacy_identifier|committed|failed
- `row_count` INTEGER NULL
- `error_message` TEXT NULL

## Table: products
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `raw_name` TEXT NOT NULL
- `canonical_name` TEXT NULL
- `form` TEXT NULL
- `strength` TEXT NULL
- `unit` TEXT NULL
- `category` TEXT NULL

## Table: batches
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `product_id` UUID NOT NULL FK -> products.id
- `lot_number` TEXT NOT NULL
- `expiry_date` DATE NULL
- `received_date` DATE NULL
- `supplier` TEXT NULL

## Table: inventory_events
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `pharmacy_id` UUID NOT NULL FK -> pharmacies.id
- `application_id` UUID NOT NULL FK -> applications.id — single canonical lineage field, no duplicate
- `batch_id` UUID NOT NULL FK -> batches.id
- `event_type` TEXT NOT NULL — receipt|sale|adjustment|damage|expiry_writeoff|transfer
- `quantity` NUMERIC(12,3) NOT NULL — supports fractional units like ml
- `unit_cost` NUMERIC(12,2) NULL
- `event_timestamp` TIMESTAMPTZ NOT NULL
- `extra_attributes` JSONB NOT NULL DEFAULT '{}'

## Table: sales
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `pharmacy_id` UUID NOT NULL FK -> pharmacies.id
- `application_id` UUID NOT NULL FK -> applications.id
- `sale_timestamp` TIMESTAMPTZ NOT NULL
- `payment_method` TEXT NULL
- `total_amount` NUMERIC(12,2) NOT NULL
- `currency` CHAR(3) NOT NULL
- `extra_attributes` JSONB NOT NULL DEFAULT '{}'

## Table: sale_lines
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `sale_id` UUID NOT NULL FK -> sales.id
- `product_id` UUID NOT NULL FK -> products.id
- `prescriber_id` UUID NULL FK -> prescribers.id
- `quantity` NUMERIC(12,3) NOT NULL
- `unit_price` NUMERIC(12,2) NOT NULL

## Table: prescribers
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `raw_name` TEXT NOT NULL
- `specialty` TEXT NULL
- `license_id` TEXT NULL

## Table: edit_audit_log
- `id` UUID PK — generated server-side via `gen_random_uuid()`
- `association_id` UUID NOT NULL FK -> associations.id
- `entity_type` TEXT NOT NULL
- `entity_id` UUID NOT NULL
- `field` TEXT NOT NULL
- `old_value` TEXT NULL
- `new_value` TEXT NULL
- `user_id` UUID NOT NULL FK -> users.id
- `edited_at` TIMESTAMPTZ NOT NULL DEFAULT now()
- `reason` TEXT NULL