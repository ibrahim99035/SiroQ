# SiroQ — Full Feature Catalog, Validation Checks, Resource Sizing, and Assurance Guarantees

## 1. Full Feature List

### 1.1 Multi-Tenancy & Administration
- Association → Pharmacy → Application → Dataset → Row hierarchy, enforced at every layer
- Association Admin can create/manage Pharmacies and Users within their own Association
- Five-role RBAC (Association Admin, Pharmacy Manager, Analyst, Data Steward, Viewer), each with a distinct permission matrix
- Scope model: a user is either locked to one Pharmacy or has Association-wide visibility, driven by a single field — no separate scope-state to manage
- Session-based auth (signed cookie), bcrypt password hashing, no public self-registration

### 1.2 Ingestion & Schema Intelligence
- Single CSV/XLSX upload per Application
- Immutable raw-file storage (Bronze layer) — original bytes never touched again
- Two-layer column classification: fuzzy header matching (bilingual — English + Arabic synonyms) + content-based statistical inference (date/numeric/categorical pattern detection)
- Confidence scoring per mapped field with color-coded review UI (green/amber/red)
- Human-in-the-loop mapping confirmation — nothing is auto-committed without review
- Multi-pharmacy-in-one-file detection with a hard gate: an unattributable file cannot be committed
- Per-Application mapping profile memory (reused on repeat uploads from the same source)
- Target-entity disambiguation when a file could represent Sales or Inventory Events
- Partial-success commit: valid rows commit even if some rows in the same file fail validation, with reasons shown per skipped row
- Unmapped source columns preserved (not discarded) via a flexible JSON attribute bag per row

### 1.3 Data Management (Data Explorer)
- Paginated, filterable table view per canonical entity (sales, inventory events, products)
- Inline cell editing
- Full edit history: every mutation logged with old/new value, user, and timestamp before it's applied
- Revert action that restores a prior value while creating a new audit entry (history is never erased)
- Partial page updates for pagination/edits without full reloads

### 1.4 Analytics & Dashboards
- Sales dashboard: KPI tiles (revenue, transaction count, average basket size), revenue-over-time chart, top-products chart
- Inventory & Waste dashboard: KPI tiles (units near expiry, waste cost this period, turnover ratio), waste-by-product chart, expiring-batches table (30/60/90-day buckets)
- Every dashboard respects the viewer's scope automatically (their assigned Pharmacy, or Association-wide) with no separate manual toggle needed for Pharmacy-locked roles

### 1.5 Security & Access Control
- Row-level tenant isolation enforced at the database layer on every tenanted table, effective even against the application's own database connection
- Two separate database roles (migration owner vs. running application) so table ownership can never silently bypass isolation policies
- Deny-by-default role checks — no fallback-to-allow branch exists anywhere in the auth code
- RTL-ready UI for Arabic-language usage

### 1.6 Documentation & Process
- Living-documentation files updated at every meaningful step, not batched
- Append-only Architecture Decision Records for every non-trivial technical choice
- Pre-logged, explicit deferral list for every future-phase capability, so nothing is a surprise gap later

---

## 2. Checks Catalog

### 2.1 Ingestion-time data validation checks (run before any row reaches the working data layer)
| Check | What it catches |
|---|---|
| Header confidence scoring | Flags any column mapping below 70% confidence for mandatory human review |
| Pharmacy-attribution gate | Blocks commit of any multi-pharmacy file with no confirmed identifier column |
| Negative value check | Rejects rows with negative quantity, unit price, or total amount |
| Duplicate-row check | Rejects exact-duplicate rows within the same upload batch (hash of mapped field values) |
| Orphan-reference check | Rejects sale lines referencing a product not present in the file or existing product table |
| Type coercion | Confirms declared canonical field types (date, numeric, text) actually parse; unparseable values fall to a row-level error list rather than corrupting the column |

### 2.2 Schema integrity checks
- Single canonical migration chain maintained deliberately (a second parallel chain is treated as a bug, not a variant)
- Every tenanted table carries its tenant identifier directly (no join-only tenancy, so isolation policies stay simple and auditable)
- A canonical schema registry document diffed against the live schema at every migration — any drift is a documented finding, not silent

### 2.3 Security checks
- Row-level isolation enabled **and** forced on every tenanted table
- Cross-tenant isolation proven with a query that has **zero** application-level filter — the database itself must be the one blocking leakage, not app-code discipline
- Table-ownership check — the running app's database role must never own the tables it queries
- Role-permission matrix checked per role, per route, including at least one deliberate negative case per role (a role that should be blocked actually gets rejected)
- No fail-open branch anywhere in the role-check code

### 2.4 Testing checks
- Golden-file ingestion suite: clean CSV, multi-pharmacy CSV, Arabic-header CSV, merged-header XLSX — each must classify and commit correctly
- Auth test suite covering session creation, expiry, and role enforcement
- Isolation test suite: proves scoping works by switching tenant context within a single database session and confirming each context sees only its own rows

---

## 3. Resource Calculation (estimates — stated assumptions, not measured benchmarks)

### 3.1 Per-row storage estimate (including index overhead)
| Table | Approx. row size | Notes |
|---|---|---|
| Sales | ~250 bytes | plus flexible JSON attributes, variable |
| Sale lines | ~180 bytes | one row per line item, typically 2–4× the sale count |
| Inventory events | ~220 bytes | plus JSON attributes |
| Edit audit log | ~300 bytes | grows only with active editing, not ingestion volume |
| Products, batches, prescribers | ~150–200 bytes each | low-growth reference tables |

### 3.2 Growth projections by scale (illustrative — adjust to real transaction counts)
| Pharmacy profile | Transactions/day | Sale lines/day (×2.5 avg) | Annual sales+lines rows | Approx. annual storage (incl. indexes) |
|---|---|---|---|---|
| Single small pharmacy | 150 | 375 | ~192,000 | ~60 MB |
| Single medium pharmacy | 800 | 2,000 | ~1,022,000 | ~320 MB |
| 10-branch Association | 5,000 | 12,500 | ~6.4M | ~2 GB |
| 50-branch Association (large chain) | 30,000 | 75,000 | ~38M | ~12 GB |

Inventory events typically run 20–40% of sale-line volume (receipts, adjustments, waste) — add roughly that proportion on top.

### 3.3 Raw-file storage estimate
Assume one upload per Application per day, average file size 200 KB–2 MB depending on branch size. A 50-branch Association uploading daily for a year: `50 × 365 × ~1MB average ≈ 18 GB/year` in raw files alone. This grows linearly and permanently (raw files are never pruned) — plan disk accordingly. This layer is the most likely to need migration to dedicated object storage before local-disk becomes impractical, likely somewhere past the 50–100 GB mark for a single-node setup.

### 3.4 Compute sizing (single-node deployment)
| Component | Small deployment (≤10 branches) | Medium (10–50 branches) | Notes |
|---|---|---|---|
| Database container | 1 vCPU / 2 GB RAM | 2 vCPU / 4–8 GB RAM | RAM matters most for query cache once dashboards query larger date ranges |
| Application container | 1 vCPU / 512 MB–1 GB RAM | 2 vCPU / 2 GB RAM, 2–4 worker processes | Server-rendered pages are cheap per request; ingestion/classification is the actual CPU spike, not page rendering |
| Disk (database volume) | 10 GB | 50–100 GB | Per the growth projections above, with headroom |

### 3.5 Scaling thresholds — when the current architecture needs to change
- A single relational-database instance is fine well past 50M rows for this workload shape (mostly append-only, simple filters) — no urgency to introduce a specialized analytical store until dashboard query latency is actually measured and found wanting.
- Local-disk raw-file storage becomes a real risk (both capacity and durability, since a local folder is trivially deletable) once raw storage crosses roughly 50–100 GB or once this runs on anything other than a single, backed-up node — that's the trigger for migrating to dedicated object storage, not a fixed calendar date.
- A single application process is fine for tens of concurrent users; add worker processes as concurrent usage grows — that's a configuration change, not an architecture change.

---

## 4. Deep Assurance — What This System Actually Guarantees (and What It Doesn't Yet)

### 4.1 Data integrity guarantees
- **Raw data is never lost or altered.** Every uploaded file is stored byte-for-byte and never rewritten. Even a bad mapping or a classification bug can be fully re-run from the original file with zero data loss.
- **Every edit is reversible.** No mutation to working data happens without a preceding audit record; a revert never erases the record it's correcting, so the full history of any value is always reconstructable.
- **Bad rows don't poison good ones.** Ingestion validation rejects individual bad rows with a stated reason rather than failing or silently corrupting an entire batch.

### 4.2 Tenant isolation guarantees
- **Isolation is enforced at the database layer, not just application logic.** A bug in application code that forgets a tenant filter cannot leak data across tenants, because the database itself enforces the boundary regardless of what the query asks for.
- **The guarantee is provable, not assumed.** An isolation test demonstrates this using a query with zero application-level filtering, which is the only way to actually trust the claim rather than take it on faith.

### 4.3 Access control guarantees
- **Deny is the default everywhere.** A role check that can't confirm permission always rejects — there is no "allow if unsure" branch anywhere.
- **Every write action requires an explicit role match**, verified per-route, not inferred from being logged in.

### 4.4 Auditability guarantees
- Every cell edit answers, permanently: what changed, who changed it, when, and what it was before — queryable at any time, not just visible in a UI at the moment of the edit.

### 4.5 Backup & disaster recovery — honest current state
- No automated database backup schedule exists by default.
- No tested restore procedure exists by default.
- Storage volumes persist across restarts but are only as durable as the underlying host disk — a host failure with no off-box backup means real data loss, despite every in-app guarantee above being sound.

**Recommendation**: a nightly database dump to a separate disk or off-box location, plus a periodic (e.g. monthly) restore drill to confirm backups are actually usable — a backup that's never been restored is not a verified backup.

### 4.6 What is explicitly NOT yet guaranteed (so nothing here is oversold)
- No encryption at rest configured for the database volume or raw files by default — data is protected by access control, not by disk-level encryption, unless added separately.
- No rate limiting or brute-force login protection on the login endpoint by default.
- No automated backup/restore.
- No load-testing has been performed — the resource numbers above are planning estimates, not measured capacity.
- No multi-node/high-availability setup — a single database instance is a single point of failure at this stage.
- Secrets (database passwords, session signing key) are plain environment variables, appropriate for development, not yet moved to a dedicated secrets manager for production use.

These six items belong in the project's tracked issues list — they're the honest boundary of what "deep assurance" currently means for this system.