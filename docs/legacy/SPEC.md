# SiroQ Specification

## 1. Overview
A single FastAPI application that ingests non-deterministic pharmacy sales/inventory CSV/Excel files, maps them into a canonical multi-tenant schema (`Association → Pharmacy → Application → Dataset → Row`), and serves all UI as server-rendered HTML pages from FastAPI itself using Jinja2 templates, HTMX for partial-page interactivity, and Chart.js for charts — all loaded via CDN `<script>` tags, no build step. PostgreSQL runs in a Docker container.

## 2. Golden Rules
| # | Rule |
|---|---|
| R1 | Never assume a column's meaning. Every file goes through detect → score → human-confirm before it's trusted. |
| R2 | Every row resolves to exactly one Pharmacy before entering analytics. If a file can't be attributed, ingestion halts and asks — never silently proceeds unattributed. |
| R3 | Every metric must be viewable at single-Pharmacy scope AND whole-Association scope, subject to the viewing user's role/scope. |
| R4 | Schema is non-deterministic and evolves after the fact. The system never hard-fails on an unfamiliar layout. |
| R5 | If an analysis needs a missing field, the system prompts — map existing column / skip for now — never fabricates or silently drops it. |
| R6 | Geo activates the moment it's present; Phase 1 scope for geo is pharmacy-level only. |
| R7 | Raw uploaded files are immutable, permanent, never edited. |
| R8 | All edits to Silver-layer data are versioned: who/when/before/after, fully reversible. |
| R9 | Analytics are added as new query modules + new template + new route — never require changing ingestion or the core schema. |
| R10 | The system surfaces insight proactively where the columns exist to support it (Phase 2+, not Phase 1). |
| R11 | Every dashboard page respects the logged-in user's scope via one shared scope-resolution mechanism. |
| R12 | Documentation is updated at every meaningful step, not batched at the end. |
| R13 | Exactly one Alembic migration chain exists in this repository, ever. |
| R14 | The running application never connects to Postgres as the migration-owning role. It connects as a separate, least-privileged role. |
| R15 | No `package.json`, no `node_modules`, no `npm`/`yarn`/`pnpm` command anywhere in this repository. |

## 3. Technology Stack
- Language: Python 3.12
- Web framework: FastAPI 0.115.0
- ASGI server: Uvicorn 0.30.6
- ORM: SQLAlchemy 2.0.35
- Migrations: Alembic 1.13.2
- DB driver: psycopg (v3, binary) 3.2.1
- Database: PostgreSQL + PostGIS, Docker container
- Templating: Jinja2 (via FastAPI's built-in `Jinja2Templates`) 3.1.4
- Interactivity: HTMX, via CDN `<script>` tag 1.9.10
- Charts: Chart.js, via CDN `<script>` tag 4.4.x
- CSS: Bootstrap 5.3 (includes official RTL bundle), via CDN 5.3.3
- Sessions/auth: Starlette `SessionMiddleware` (itsdangerous signed cookie) + `passlib[bcrypt]` for password hashing
- File parsing: pandas + openpyxl
- Fuzzy header matching: rapidfuzz 3.9.6
- Multipart uploads: python-multipart 0.0.9
- Background jobs (in-process, no external broker): APScheduler 3.10.4
- Testing: pytest 8.3.2 + httpx TestClient
- Config: pydantic-settings 2.5.2

## 4. Repository Structure
Exact directory tree as specified in the project prompt — see the repository source.

## 5. Infrastructure
- `docker-compose.yml` — db (postgis/postgis:16-3.4) + app services
- `Dockerfile` — python:3.12-slim with system deps
- `requirements.txt` — exact pinned dependencies
- `Makefile` — up, down, logs, migrate, makemigration, seed, test, shell, psql

## 6. Auth & RBAC
- Five roles: association_admin, pharmacy_manager, analyst, data_steward, viewer (Postgres enum)
- Scope model: `pharmacy_id` NULL = association-wide; set = locked to one Pharmacy
- Permission matrix as specified
- Session mechanism: `POST /login` sets `request.session["user_id"]`; `get_current_user` dependency; `require_role` factory

## 7. Database Schema
Exact table definitions as specified — associations, pharmacies, users, applications, mapping_profiles, datasets, products, batches, inventory_events, sales, sale_lines, prescribers, edit_audit_log.

## 8. Pages & Routes
Exact route list as specified — from /login through /dashboards/sales and /dashboards/inventory.