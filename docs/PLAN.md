# SiroQ Analysis Service — Plan (2026-09-21)

## Goal

Turn this codebase into **one self-contained FastAPI analysis service**: a caller
submits an "application" (a named group) with one or more files in a single
request -> the service deeply analyzes them (profile + classify + data-quality +
best-effort domain analytics), persists raw files + results, and serves the
saved results back on later requests via a key-protected JSON API for other
microservices.

Decisions:
- Analysis output: full profiling + data quality, plus best-effort domain analytics.
- Persistence: Postgres (metadata/results) + local disk (raw files). Reuses
  docker-compose + alembic.
- "Application" = flat top-level grouping. No tenancy / RBAC / RLS.
- Static API key auth via `X-API-Key`.

## Architecture

```
Caller service --(X-API-Key)--> POST /api/v1/analyze (multipart: application + files[])
                                    |
                          +-----------------------+
                     raw files to disk          analysis pipeline
                     (storage/, sha256)         profile + classify + quality + domain
                                    |                       |
                                    v                       v
                           Postgres: applications / files / analyses (report JSONB)
                                    |
                          +-----------------------+
                     GET /api/v1/applications/{id}/analyses/{aid}  (full saved report)
                     GET /api/v1/applications/{id}  (files + analyses summary)
                     GET /api/v1/files/{fid}        (raw download)
```

## API surface (v1, all but /health behind X-API-Key)

| Method | Path | Purpose |
|---|---|---|
| POST | /api/v1/analyze | One-shot: multipart application_name + files[] -> create/find app, ingest, analyze, persist, return {application_id, analysis_id, summary, report} |
| POST | /api/v1/applications | Create empty application (JSON {name, metadata}) |
| POST | /api/v1/applications/{id}/files | Upload files to an existing app -> analyze synchronously |
| POST | /api/v1/applications/{id}/analyze | Re-run analysis over already-uploaded files |
| GET  | /api/v1/applications/{id} | App + file list + analyses list (light) |
| GET  | /api/v1/applications/{id}/analyses/{aid} | Full saved analysis report |
| GET  | /api/v1/files/{fid} | Download original raw file |
| GET  | /health | Open liveness probe |

## Analysis report shape (stored as JSONB on `analyses`)

```
summary: file_count, total_rows, categories_detected, data_quality_score,
         findings_count, domain_metrics_headline
files[] each:
  profile         columns, dtypes, row_count, null%, unique/cardinality,
                  numeric/date stats, top categories, sample rows
  classification  category scores + per-canonical-field {score,status,suggested_mapping}
  data_quality    duplicated rows, negatives, type-coercion errors, empty file,
                  unmapped columns
  domain_analytics  category-gated engines or {skipped, reason}
```

## Domain engines (ported SQL -> pandas, best-effort)

- sales: revenue, tx count, avg basket, daily series, payment mix, margin (if
  cost column), top products
- inventory: qty on hand, expiry risk 30/60/90 + value-at-risk, waste/damage,
  ABC/XYZ value classification, dead/static stock
- products / patients / prescriptions / payers / suppliers-pos: category counts,
  demographics, fill/refill proxies, billed-vs-paid + rejections, fill/lead-time

Missing required columns -> engine reports `{skipped: "missing columns: ..."}`.

## Filesystem changes

New:
- app/config.py, app/database.py, app/security.py, app/main.py
- app/models/service_models.py
- app/routers/applications.py, app/routers/analyses.py
- app/analytics_service/{pipeline, profile, quality, storage, classification, analytics}.py
  (extends existing ingestion.py)
- alembic/versions/20260921_006_analysis_service.py
- tests (roundtrip, quality, auth)

Rewrite:
- alembic/env.py

Modified:
- docker-compose.yml, .env, .env.example

Archived / deleted:
- Legacy docs -> docs/legacy/ (SPEC, FEATURE_CATALOG, GAP_ANALYSIS,
  LARGE_FILE_ANALYSIS, SECURITY_SPEC, SCHEMA_REGISTRY, swagger.json,
  showcase.html, docs/USER_FLOWS.md)
- app/services/analytics.py (broken SQL-bound) -> deleted

## Database migration (006, single chain)

- Drop legacy tenancy/RLS tables (associations, pharmacies, users,
  mapping_profiles, datasets, legacy applications, products, batches,
  inventory_events, sales, sale_lines, prescribers, patients, prescriptions,
  payers, suppliers, purchase_orders, alert_rules, alerts, edit_audit_log,
  daily snapshots) + user_role_enum.
- Create flat service tables:
  - applications(id uuid pk, name, metadata jsonb, created_at)
  - files(id uuid pk, application_id fk cascade, original_filename, stored_path,
    sha256, size_bytes, file_type, status, error_message, created_at)
  - analyses(id uuid pk, application_id fk cascade, status, summary jsonb,
    report jsonb, error_message, created_at, completed_at)
  - indexes on application_id

## Verification

- alembic upgrade head against dockerized Postgres (port 5433)
- pytest suite (roundtrip, quality-on-messy-file, api-key auth)
- curl smoke with X-API-Key

## Notes

- Analysis runs synchronously in-request (batch/background is a later add).
- Migration 006 drops legacy tables and any old data they hold.