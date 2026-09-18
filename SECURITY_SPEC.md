# Security Hardening Spec — Phase 2 (pilot-hardening)

Scope of the application-level hardening applied on top of the Phase 1
correctness work and the `20260918_005_pilot_hardening` migration. Every item
below is covered by tests in `tests/test_security.py` (36 tests total in the
suite, all passing).

## 1. Threat model addressed

| Threat | Vector | Control |
|---|---|---|
| Path traversal | uploaded filename / zip entry names escaping the bronze root | `safe_filename()` basename reduction + allow-list charset |
| Zip bomb | small `.zip` that decompresses to gigabytes | cumulative uncompressed cap in `extract_zip` |
| Oversized upload | 10 GB POST buffering the whole body in RAM | `MAX_UPLOAD_BYTES` pre-read cap (reads limit+1 byte only) |
| Forged writes | cross-site form POST to any state-changing route | `SameOriginGuard` middleware (Origin/Referer vs Host) |
| Mass assignment | client naming a non-editable column (`field=association_id`) | `EDITABLE_SALE_FIELDS` allow-list, enforced on edit *and* revert |
| Cross-tenant writes | pharmacy-scoped user POSTing another branch's `row_id` | explicit `row.pharmacy_id == user.pharmacy_id` check on every write |
| Type confusion | `"not-a-number"` into `total_amount`, garbage into a timestamp | typed casting (`_cast`) → `400`, not a 500 or silent string |
| Forgeable sessions | shipped `SECRET_KEY` used in production | pydantic model validator refuses startup outside `development` |
| Connection leak | bare `return SessionLocal()` never closed | yield-style `get_session` with `finally: db.close()` |

## 2. Controls in detail

### 2.1 Filename sanitisation — `app/services/ingestion.py`
`safe_filename(name)` reduces any name to a bare basename (`\` normalised to
`/` first), strips leading dots, replaces every character outside
`[A-Za-z0-9._ -]` with `_`, and falls back to `"upload"` for an empty result
(`..` → `upload`). Capped at 120 chars. Used by `save_bronze`,
`extract_zip`, and `classify_zip` — so `bulk_mapping_page` (classify) and
`bulk_mapping_confirm` (extract) always agree on keys. The human-readable
original is preserved on `datasets.original_filename` and is only ever
rendered as text.

### 2.2 Zip handling — `extract_zip`
Entries are iterated via `infolist()`; directory entries skipped; a running
`total += info.file_size` is compared against `max_uncompressed` *before*
reading, raising `ValueError("Zip archive expands beyond the allowed size
limit")`. Call sites pass `request.app.state.settings.zip_cap()`
(default 200 MB). Zip-slip names come out neutralised (`../../evil.csv` →
`evil.csv`), and the test asserts no extracted name contains `/` or `..`.

### 2.3 Upload cap — `POST /applications/{id}/upload`
`file.file.read(max_bytes + 1)` detects oversize without buffering the whole
body; the user gets a friendly "exceeds the N MB upload limit" page instead of
an OOM. Default 50 MB, configurable via `MAX_UPLOAD_BYTES`.

### 2.4 Same-origin guard — `app/middleware.py`
Pure ASGI middleware wrapping the session middleware (added after it, so it
runs first). On any `POST` with an `Origin` or `Referer` whose authority does
not equal the request's `Host` authority → `403 cross-origin request blocked`.
Comparing against `Host` (not the server socket) matters: uvicorn binds
`0.0.0.0`, browsers send `Host: localhost:8000`, and a cross-site attacker can
control neither the victim's `Host` nor their own `Origin` matching it.
Requests with neither header are allowed — browsers always send one on a
cross-site post, which is exactly the request being rejected. Verified live:
`Origin: http://evil.example` → 403; same-origin login and dashboard → 200.

### 2.5 Data Explorer write path — `app/routers/applications.py`
- `EDITABLE_SALE_FIELDS = {sale_timestamp, payment_method, total_amount,
  transaction_ref, currency}` — anything else, including the grid's previous
  free-form `entity` parameter, is refused with `400 field not editable`.
- `_load_editable_sale` enforces pharmacy scope on writes: a pharmacy-scoped
  user editing another branch's row gets `403`. RLS alone did not provide this.
- `_cast` converts client values to column types: `total_amount` through
  `ingestion._number` (rejects non-numeric), `sale_timestamp` through
  `ingestion.parse_date` (raises → 400), others stay text.
- Audit-before-edit is preserved, and **revert writes a new audit row** with
  reason `"revert"`, so the trail is append-only.

### 2.6 Secret validator — `app/config.py`
`Settings._require_real_secret_outside_dev` raises at import time when
`ENVIRONMENT != "development"` and `SECRET_KEY` equals the shipped
`DEV_SECRET_KEY` default. Tests instantiate `Settings(ENVIRONMENT="production",
SECRET_KEY=DEV_SECRET_KEY)` and assert `ValidationError`.

## 3. Model/schema drift found and fixed
Two columns declared `Mapped[str]` with **no foreign key** — SQLAlchemy
therefore inferred `VARCHAR` while the schema column is `uuid`, so every
insert failed with `DatatypeMismatch`:
- `edit_audit_log.entity_id` — **this silently broke every Data Explorer edit
  and every revert in production** (the audit-first insert raised before the
  edit could commit). Fixed with `Uuid()`.
- `alerts.entity_id` — would have broken alert creation the same way. Fixed.
Both carry comments in `models.py`; a drift scan over all 79 uuid columns in
`information_schema.columns` confirms these were the only two with neither a
FK nor an explicit `Uuid()`.

## 4. Test coverage — `tests/test_security.py`
1. `safe_filename` strips directories, backslashes, leading dots, empty → fallback.
2. `save_bronze` confines a `../../` name inside the tmp root (`commonpath`).
3. `extract_zip` neutralises zip-slip; extracted keys are bare names.
4. `extract_zip` raises `ValueError` past the uncompressed cap.
5. Oversized upload (16-byte limit, 100-byte file) renders the rejection page.
6. Explorer rejects `field=association_id` with `400 not editable`.
7. Explorer rejects a non-numeric `total_amount` with `400`.
8. An allowed edit persists (12.50) **and** writes an `EditAuditLog` row.
9. `Settings` refuses the dev secret in production; accepts a real one.
10. Cross-site `Origin` POST → `403 cross-origin request blocked`.
11. Cross-site `Referer`-only POST → 403 as well.

Cross-tenant fixtures follow `test_rls.py`: second association created via the
owner engine (`MIGRATIONS_DATABASE_URL`), rows inserted under
`app.current_association_id`, cleaned up in reverse dependency order.

## 5. Verification performed
- `docker compose exec app pytest -q` → **36 passed**.
- Live smoke test against the running container: health 200; login without
  Origin 200; login with `Origin: http://evil.example` **403**; same-origin
  login 200 → dashboard 200.
- `python -m py_compile` on every touched module.
- Round-trip check: `_grid_rows.html` grid posts (`row_id`, `field`,
  `value`) match the route signature exactly after removing the dead
  `entity` hidden input.

## 6. Known limitations / follow-ups
- Same-origin guard is host-based; if a reverse proxy rewrites `Host`,
  forward the original as `X-Forwarded-Host` and extend the allowed set.
- Explorer allow-list covers the `sales` grid only; other tables are read-only.
- `bulk_mapping_confirm` reads the whole zip via `extract_zip`; the cap bounds
  memory, but true streaming extraction would remove it entirely.
- Login brute-force protection and rate limiting remain open (see SPEC.md §4.6).

