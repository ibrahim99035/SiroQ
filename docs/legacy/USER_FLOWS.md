# SiroQ — End-to-End Flows & User Stories

> Verified against the code on branch `pilot-hardening` (commit `17227a3`).
> Scope: 5 roles · 28 routes · 9 analytics engines · 26 tables behind 24 RLS policies.

---

## 1. Roles & Permissions

SiroQ is multi-tenant: an **Association** owns **Pharmacies** (branches), and every
user belongs to an association and (optionally) a single pharmacy. Row-level
security in PostgreSQL enforces the tenant boundary — the UI can only show what
the database will return for the signed-in user.

| Role | Dashboards | Ingest (upload/map/commit) | Data Explorer (edit/revert) | Admin |
|---|---|---|---|---|
| `association_admin` | ✅ | ✅ | ✅ | ✅ |
| `pharmacy_manager` | ✅ | ✅ | ✅ | — |
| `data_steward` | ✅ | ✅ | ✅ | — |
| `analyst` | ✅ | — | — | — |
| `viewer` | ✅ | — | — | — |

*Enforced by the `require_role(...)` dependency in `app/dependencies.py` — deny
by default, no fail-open branch. Route groups: `INGEST_ROLES`, `EDIT_ROLES`,
`ALL_ROLES`, `ADMIN_ROLE`.*

**Primary personas**

- **Amira — Association Admin**: sets up the tenant, branches, and people; owns governance.
- **Mostafa — Pharmacy Manager**: uploads his branch's sales exports and fixes data quality.
- **Salma — Data Steward**: curates committed rows in the Explorer; audits every change.
- **Hana — Analyst**: reads dashboards; never touches ingestion.
- **Omar — Viewer**: read-only visibility, e.g. an association chairman.

---

## 2. Notation

- ✅ success path · ⚠️ guard/decision · ❌ error path
- `(GET)` / `(POST)` = HTTP method; braces are URL parameters.
- "Silver" = validated, canonical tables (sales / inventory) that analytics read.
---

## 3. Flow 1 — First-Time Setup (Association Admin)

**Actors:** Amira (association_admin)

1. **Sign in** — `(GET /login)` → split-screen login card. `(POST /login)` validates
   credentials against the `users` table and issues a session cookie (`SameSite=Lax`).
   ❌ Wrong credentials → inline error, no session.
   ⚠️ Every subsequent route requires the session; anonymous visitors are redirected (303) to `/login`.
2. **Land on Administration** — `(GET /admin)` → three tiles: Associations, Pharmacies, Users.
3. **Create the association** — `(GET /admin/associations)` → table + create form,
   `(POST /admin/associations)` (name required; country & default currency, e.g. EGP).
4. **Create branches** — `(GET /admin/pharmacies)` → `(POST /admin/pharmacies)`
   (name + association). One row per pharmacy branch.
5. **Invite people** — `(GET /admin/users)` → `(POST /admin/users)` (email, full
   name, role from the 5 valid roles, optional pharmacy scope). A user with a
   pharmacy scope is **locked to that branch** by RLS; without scope they see the
   whole association.
   ⚠️ Invalid role names are rejected server-side (never trusted from the client).

**Exit criterion:** ≥1 association, ≥1 pharmacy, ≥1 user per role needed.

---

## 4. Flow 2 — Single-File Ingestion (the core loop)

**Actors:** Mostafa (pharmacy_manager), Salma (data_steward), or Amira.

1. **Create an application** — `(GET /applications)` lists the association's data
   feeds (scoped by RLS) → `+ New Application` → `(POST /applications/new)`
   (name, source type, optional single-pharmacy scope).
   *An application is a reusable pipeline for related uploads. If it is scoped to
   one pharmacy, every dataset commits there; otherwise the file itself must
   identify the pharmacy (step 5).*
2. **Upload** — from the application detail page → `(GET /applications/{id}/upload)`
   shows a drop-zone tile → `(POST /applications/{id}/upload)` accepts
   **CSV / XLSX / XLS / ZIP**.
   ⚠️ Guards run before anything is parsed: size cap, sanitized filenames
   (`safe_filename`, 128 chars), zip entries scanned one-by-one with a total
   uncompressed-size cap (zip-bomb guard) and path-traversal protection (zip-slip guard).
   ✅ The file is stored **byte-for-byte in the bronze layer** (`bronze_file_path`)
   and a `datasets` row is created (`status=uploaded`).
   ❌ Rejected file → error page; nothing stored, nothing parsed.
3. **Split & classify** — every sheet / zipped file becomes a source. If more than
   one source is detected the **bulk flow (Flow 3)** is used; otherwise the
   classifier scores each canonical field against the columns
   (`classification.field_scores`: suggested mapping, sample values, confidence %).
4. **Confirm mapping** — `(GET /datasets/{id}/mapping)` (step 2 page) renders one
   select per canonical field, pre-set to the suggestion, with sample values and a
   confidence badge (≥90 green / 70–89 amber / <70 red).
5. **Multi-pharmacy decision** ⚠️ — if the application has **no** pharmacy scope,
   the page asks for the **pharmacy identifier column** (with a smart suggestion).
   This choice is a **hard gate**: `(POST /datasets/{id}/confirm)` without it sets
   `status=needs_pharmacy_identifier` and returns a *blocked* result page — the
   dataset cannot be committed until a column is chosen.
6. **Commit** — `(POST /datasets/{id}/confirm)`:
   - reads the bronze file, applies the confirmed field map;
   - validates every row; **invalid rows are excluded with reasons** (never
     silently dropped — partial success is honest);
   - writes valid rows to silver (currency from the association, e.g. EGP);
   - saves the mapping as a **profile** so future uploads reuse it;
   - marks the dataset `committed` with `row_count`.
7. **Result page** — `step3_result`: success banner, "Committed N row(s)", a table
   of excluded rows with per-row reasons, and next-step buttons
   (Data Explorer / Sales dashboard / back to the application).

---

## 5. Flow 3 — Bulk Ingestion (multi-file / multi-sheet)

**Trigger:** the upload contained more than one file or sheet.

1. `(GET /applications/datasets/{id}/bulk_mapping)` renders **one card per source**
   (file or sheet), each with its own classification table and — for multi-pharmacy
   applications — its **own pharmacy-identifier select**.
2. The reviewer adjusts mappings per source; the page serializes
   `field_maps[source][field]` plus `pharmacy_identifier_columns[source]` into
   hidden inputs on submit.
3. `(POST /applications/datasets/{id}/bulk_mapping)` commits each source through
   the same validation as Flow 2 and reports per-source results.

**Use cases:** one workbook with a "Cairo branch" and "Giza branch" sheet; a ZIP
of daily exports from several branches.
---

## 6. Flow 4 — Data Explorer (Edit & Revert)

**Actors:** Salma (data_steward), Mostafa (pharmacy_manager), or Amira. Analysts and viewers have no access (403).

1. **Open** — `(GET /applications/{id}/explorer)` shows the live silver grid for
   the application. `(GET /applications/{id}/explorer/rows)` loads rows via htmx.
2. **Inline edit** — edit a `total_amount` cell and save →
   `(POST /applications/{id}/explorer/cell)`. The change writes to silver **and
   appends an entry to the append-only audit log** (who, when, old → new).
3. **Revert** — any prior edit can be reverted in one click
   `(POST /applications/{id}/explorer/revert/{edit_id})`, restoring the previous
   value while keeping the audit trail intact (reverts are themselves logged).

**Guarantees:** every mutation is traceable to a user and timestamp; tenant scope
comes from the session user and is enforced again by RLS at the database.

---

## 7. Flow 5 — Dashboards (Read Path)

**Actors:** all 5 roles. Scope = the user's pharmacy if locked, else the whole association.

1. **Sales dashboard** — `(GET /dashboards/sales)`
   KPI tiles (Total Revenue EGP · Transactions · Avg Basket) + revenue-over-time
   area chart, sourced only from committed silver rows.
2. **Inventory & waste dashboard** — `(GET /dashboards/inventory)`
   KPI tiles (Units near expiry 30d · Waste cost · Turnover ratio) + waste-by-product
   bar chart; the expiring-batches view highlights 30/60/90-day horizons with severity.

⚠️ Empty state: with no committed data the tiles render zeros — the dashboards are
always honest, never blank.

---

## 8. Flow 6 — Analytics Engines (service layer, 9 engines)

Implemented in `app/services/analytics.py`; dashboards surface the first two today,
the rest are pilot-ready APIs awaiting UI cards:

| # | Engine | Question it answers |
|---|---|---|
| 1 | `sales_kpis` | Revenue, transactions, basket size |
| 2 | `inventory_kpis` | Expiry exposure, waste cost, turnover |
| 3 | `inventory_optimization_analytics` | What to reorder / liquidate first |
| 4 | `abc_xyz_classification` | Which SKUs deserve tight control (ABC × XYZ grid) |
| 5 | `sales_margin_analytics` | Margin per product / branch / period |
| 6 | `payer_performance_analytics` | Insurer & payment-method performance |
| 7 | `adherence_analytics` | Repeat-purchase / medication adherence windows |
| 8 | `controlled_substance_analytics` | Scheduled-drug movement & audit flags |
| 9 | `procurement_supplier_analytics` | Supplier lead-time & purchasing patterns |

---

## 9. User Stories (by role)

### Association Admin — Amira

| ID | Story | Acceptance |
|---|---|---|
| A1 | As Amira I register my association so my pharmacies share one workspace | Association appears in `/admin/associations`; currency defaults to EGP |
| A2 | As Amira I add each branch so data can be scoped per pharmacy | Branch rows exist and are selectable as user scope |
| A3 | As Amira I create users with roles so least privilege is enforced | Role must be one of the 5 valid values; wrong role is rejected server-side |
| A4 | As Amira I keep a user scoped to one branch so they never see other branches | Their queries return only their pharmacy's rows (RLS-enforced) |
| A5 | As Amira I get 403 on pages outside my role so the system fails closed | No page, action, or API is reachable without an allowed role |

### Pharmacy Manager — Mostafa

| ID | Story | Acceptance |
|---|---|---|
| P1 | As Mostafa I create an application for my sales exports so uploads share one pipeline | Application appears in the list, scoped to my association |
| P2 | As Mostafa I upload CSV/XLSX/ZIP without worrying about malicious files | Oversized, zip-bomb, or path-traversal files are rejected before parsing |
| P3 | As Mostafa I confirm the system's column guesses so mapping takes seconds | Suggestions pre-selected; I override per field; confidence is visible |
| P4 | As Mostafa I pick the pharmacy column on multi-branch files so rows land in the right branch | Commit is blocked until the column is chosen; rows resolve per value |
| P5 | As Mostafa I see which rows failed and why so I can fix the source file | Excluded rows table lists row numbers and reasons; valid rows still commit |
| P6 | As Mostafa I reuse the saved mapping profile so next week's upload is one click | Profile auto-applies on the next upload for the same application |

### Data Steward — Salma

| ID | Story | Acceptance |
|---|---|---|
| S1 | As Salma I fix wrong totals inline so silver data stays trustworthy | Cell edit persists and is audit-logged with old/new values |
| S2 | As Salma I revert any edit so mistakes are never permanent | One-click revert restores the prior value and is itself logged |
| S3 | As Salma I know nothing is edited off the record so audits pass | Append-only audit log; no update-in-place history loss |

### Analyst — Hana

| ID | Story | Acceptance |
|---|---|---|
| AN1 | As Hana I read sales KPIs and trends so I can brief the association | Dashboard scoped to my association (or my pharmacy if locked) |
| AN2 | As Hana I cannot upload or edit so the read path stays clean | Ingestion and explorer routes return 403 for my role |
| AN3 | As Hana I trust the numbers so decisions follow | Only committed silver rows are charted; excluded rows never appear |

### Viewer — Omar

| ID | Story | Acceptance |
|---|---|---|
| V1 | As Omar I view dashboards read-only so I stay informed | Same dashboards, no actions anywhere |
| V2 | As Omar I can never mutate data so governance is trivial | All POST routes reject my role (403) |
---

## 10. The Complete Journey (one page)

```
Sign in
  └─> Admin (Amira): association ─> pharmacies ─> users (roles + scope)
        └─> Manager (Mostafa):
              create application ─> upload (guarded) ─> bronze (byte-for-byte)
                └─> classify ─> [single: mapping page | multi: bulk cards]
                      └─> pharmacy column? (hard gate for multi-branch files)
                            └─> COMMIT ─> silver + mapping profile + per-row reasons
                                  ├─> Explorer (Salma): inline edit ─> audit log ─> revert
                                  ├─> Sales dashboard ─> KPIs + trend
                                  └─> Inventory dashboard ─> expiry / waste / turnover
                                        └─> 9 analytics engines (service layer)
```

Failure is first-class: rejected files, blocked multi-pharmacy commits, and
excluded rows all return explicit, human-readable reasons.

---

## 11. Guard Rails on Every Flow

| Guard | Where it fires | What it prevents |
|---|---|---|
| Session + `SameSite=Lax` cookie | every route | anonymous access |
| `SameOriginGuard` middleware | every POST | cross-site form posts (CSRF) |
| `require_role(...)` (deny by default) | every route | privilege escalation, fail-open bugs |
| Row-Level Security (24 policies) | every query | cross-tenant data leakage |
| Size cap + `safe_filename` | upload | disk exhaustion, filename attacks |
| Zip-bomb & zip-slip checks | ZIP upload | memory/disk bombs, path traversal |
| Server-side role validation | user creation | client-trust bugs |
| Audit log (append-only) + revert | explorer edits | silent or untraceable data changes |
| Mapping profile reuse | commit | mapping drift between uploads |

---

## 12. Persona × Feature Matrix

| Feature | Admin | Manager | Steward | Analyst | Viewer |
|---|---|---|---|---|---|
| Manage tenants / branches / users | ● | | | | |
| Create applications | ● | ● | ● | | |
| Upload & map files | ● | ● | ● | | |
| Commit / bulk-commit datasets | ● | ● | ● | | |
| Edit & revert cells (Explorer) | ● | ● | ● | | |
| Sales & inventory dashboards | ● | ● | ● | ● | ● |
| 9 analytics engines (API today) | ● | ● | ● | ● | ● |
| Audit trail visibility | ● | ● | ● | | |

---

## 13. Honest Limitations (what we do NOT yet guarantee)

- No rate limiting on login or upload endpoints.
- No encryption at rest (postgres volume) — TLS in transit is deployment-dependent.
- No automated backups / disaster recovery yet.
- Single-node deployment; no HA story for the pilot.
- Behind a proxy, IP throttling/rate limits must be configured at the proxy.
- Analytics 3–9 are API/service-layer only — dashboard cards are pending.
