# SiroQ — Analysis Service Architecture

This document describes how the analysis service (`app/analytics_service/`) is
structured, how data flows through a run, and how the **registry** pattern makes
every analysis feature pluggable.

> For the "how do I add X?" recipes, see [ADDING_FEATURES.md](ADDING_FEATURES.md).

## Module map

```
app/
├── main.py                        FastAPI app + dashboard mount
├── config.py                      Settings (env-driven; DATABASE_URL, STORAGE_PATH, API key)
├── database.py                    SQLAlchemy engine/session plumbing
├── security.py                    API-key auth dependency
├── models/service_models.py       ORM: Application / File / Analysis
├── routers/
│   ├── applications.py            app CRUD, file uploads, analyze trigger
│   ├── analyses.py                analysis reports + forecast/preview/reporting endpoints
│   └── health.py / files.py       health & storage helpers (as present)
├── storage.py                     bytes on disk (<STORAGE_PATH>), read_bytes/get_meta
└── analytics_service/
    ├── registry.py                The pluggability core (see below)
    ├── ingestion.py               readers → DataFrame(s), schema-category detection
    ├── profile.py                 column profiles (numeric/categorical)
    ├── classification.py          canonical-field scoring (header mapping)
    ├── quality.py                 data-quality checks + 0-100 score
    ├── engines/                   one domain-analytics engine per business category
    │   ├── __init__.py            dispatcher run_domain_analytics()
    │   ├── sales.py, inventory.py, reference.py, purchase_orders.py
    │   └── _helpers.py            shared column-picking helpers
    ├── forecast.py                pure-python statistical forecast methods
    ├── pipeline.py                orchestrates one deep-analysis run
    └── analytics.py               compatibility shim for the domain-analytics API
```

## Data flow of a run

`POST /analyses` (router) → `pipeline.analyze_application(app, files)`:

1. **Per file**: bytes are read from `STORAGE_PATH`, then `ingestion.read_file()`
   dispatches to a registered reader (csv / excel / json) keyed by extension.
   Workbooks with several sheets get a multi-sheet report section; otherwise the
   primary frame is analyzed.
2. **`_analyze_dataframe(df)`** runs the registered *pipeline stages* on one
   frame, in order:
   - `profile` — column profiles
   - `classification` — canonical field mapping (`fmap`) + confidence scores
   - `quality` — data-quality checks + aggregate score
   - `domain` — schema-category detection → domain analytics for the top category
3. **`analyze_application`** folds per-file sections into a `summary` (file
   count, total rows, categories detected, average quality score, findings) and
   a persisted `report` (all sections + engine version + timestamp). Everything
   passes through `sanitize()` to become JSON-safe (numpy/NaN → None etc.).

## The registry pattern

`app/analytics_service/registry.py` defines a generic `Registry` (ordered
name → `Entry(name, fn, meta)`) and five named registries plus their decorators:

| Registry        | Decorator            | Extends…                                     |
|-----------------|----------------------|----------------------------------------------|
| `domain_engines`  | `@domain_engine(category, order=…)`   | which business domains have analyses |
| `quality_checks`  | `@quality_check(name, penalty=…, needs_field_scores=…)` | what counts against the quality score |
| `forecast_methods`| `@forecast_method(name, description=…, order=…)` | what forecast candidates compete |
| `file_analyzers`  | `@file_analyzer(name, order=…)`      | what runs on each DataFrame |
| `file_readers`    | `@file_readers.register(file_type)`   | what file formats `read_file` accepts |

Collecting is done at **import time**: each decorated function registers itself
when its module is imported. `pipeline.py`, `quality.py`, `forecast.py` and
`ingestion.py` only *consume* the registries, so adding a feature never edits
existing logic.

Key contract details:

- A name may hold multiple entries when `allow_dup=True` (used by the two
  `moving_average` forecast windows). Plain accessors return the **first**
  match; `.all()` yields everything in registration order.
- Duplicate non-`allow_dup` registration raises `ValueError` at import time —
  loud, early feedback that two modules now own the same slot.
- Per-run state lives in a `ctx` dict for pipeline stages (`df`, `field_scores`,
  `fmap`, …) so stages can share work without each other's import coupling.

## Engines (domain analytics)

Each `@domain_engine("<category>")` is a function

```python
def engine(df: pd.DataFrame, fmap: dict) -> dict: ...
```

where `fmap` maps *canonical* field names (`total_amount`, `sale_timestamp`, …)
to the file's actual column names (from `classification`). Engines use the
helpers in `engines/_helpers.py` (`pick_col`, `numeric`, `safe_sum`,
`col_sum`, `categorical_counts`) and return a JSON-safe report. The dispatcher
`engines.run_domain_analytics(df, category, fmap)` selects the engine by
category and returns `{"skipped": ...}` when none is registered.

## Quality scoring

`run_quality_checks(df, field_scores)` executes every registered
`quality_check` in order and computes `100 - Σ penalty` for non-passing checks.
An unreadable/empty frame still runs the checks (self-passing) but scores `0`
outright (historical contract). A check that needs `field_scores` is marked
`needs_field_scores=True` and receives the classification map.

## Forecasting

`forecast.py` registers one builder per method (`naive`, `mean`,
`moving_average`, `linear`, `damped_trend`, `seasonal`). During a run:

1. A holdout (last `min(7, n//3)` points) scores every registered candidate via
   the ordered `_MODELS` table.
2. The winner is `min` by (holdout score, `order` tie-break).
3. The winner is refit on the full series and extrapolated `horizon` steps.

The two `moving_average` variants (win 3, win 7) are registered under one name;
the last-registered variant wins the holdout score, the first-registered wins
the final fit (deliberate, history-preserving choice).

## Testing

- Tests run against a **throwaway Postgres DB** (`siroq_test`) with an
  overridden `STORAGE_PATH` — never against the live DB. See
  `pytest.ini`/`tests/conftest.py`.
- `conftest.py` truncates `analyses/files/applications` and wipes storage each
  test, so the live demo/data-sample apps are never touched.

## Config / infra

- `docker-compose.yml` provides PostGIS on port 5433 (roles `siroq`/`siroq_app`,
  databases `siroq` live, `siroq_test` for tests).
- `alembic/` owns migrations (`MIGRATIONS_DATABASE_URL` uses the `siroq` role);
  the app connects as `siroq_app`.
- Files (`files.original_filename`, `sha256`, `size_bytes`, `stored_path`) live
  on disk under `STORAGE_PATH` (default `./data/storage`), mirrored into the
  `files` table.