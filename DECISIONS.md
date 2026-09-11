# DECISIONS.md

Empty ADR registry. First ADRs:

## ADR-001: Two-role Postgres split for RLS safety

**Context**: Previous attempts at this system connected the FastAPI application directly as the migration-owning Postgres role. This silently bypassed Row-Level Security policies because table owners bypass RLS by default in PostgreSQL.

**Decision**: Create two separate Postgres roles:
- `siroq`: owns the schema and runs Alembic migrations
- `siroq_app`: the role the FastAPI application actually connects as

**Alternatives considered**: Consolidating to one role with explicit REVOKE/GRANT on every table — too error-prone and maintenance-heavy.

**Consequences**: The two-role split is permanent. Future migrations must use `ALTER DEFAULT PRIVILEGES` (as in `docker/init-db/01_create_app_role.sql`) so that tables created later automatically grant the correct privileges to `siroq_app`.

---

## ADR-002: Server-rendered HTML instead of a JS frontend framework

**Context**: The long-term scope of SiroQ includes a JS frontend framework (React/Next.js/Vue), but Phase 1 explicitly excludes these.

**Decision**: Build Phase 1 as a server-rendered HTML application using FastAPI + Jinja2 + Bootstrap 5.3 + HTMX + Chart.js via CDN. No `package.json`, no `node_modules`, no npm/yarn/pnpm commands anywhere in the repository.

**Alternatives considered**: 
- React/Next.js: Would require build step, Node.js setup, API client patterns — out of scope for Phase 1.
- Vue: Same as React — build step and tooling required.

**Consequences**: Frontend behavior is HTML + HTMX + Chart.js rendered by Jinja2. All interactivity is via HTMX calling FastAPI endpoints for partial page updates. Charts are rendered via Chart.js CDN scripts. This keeps the repository purely Python-powered with zero JavaScript build dependencies.