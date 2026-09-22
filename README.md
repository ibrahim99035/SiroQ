# SiroQ Analysis Service

Deep-analysis service for application data files. Ingest files (CSV / Excel /
JSON) for an application in one request, and get back a persisted report:
column profiles, canonical-field classification, a 0–100 data-quality score,
and best-effort domain analytics — plus deterministic statistical forecasting
and live file preview/series endpoints.

- Consumer API guide: [`docs/API_INTEGRATION.md`](docs/API_INTEGRATION.md)
- Architecture & extensibility: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Adding features: [`docs/ADDING_FEATURES.md`](docs/ADDING_FEATURES.md)

## Run locally

```bash
docker compose up -d db        # PostGIS on :5433 (dev credentials in docker/init-db)
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Dashboard: http://localhost:8000/dashboard/ · OpenAPI: http://localhost:8000/docs

## Test

Tests run against a throwaway database and throwaway storage — never the live
data:

```bash
DATABASE_URL="postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5433/siroq_test" \
MIGRATIONS_DATABASE_URL="postgresql+psycopg://siroq:siroq_dev_password@localhost:5433/siroq_test" \
STORAGE_PATH="/tmp/siroq_test_storage" \
.venv/bin/python -m pytest -q
```

## Build the package

```bash
.venv/bin/python -m build     # -> dist/*.whl + dist/*.tar.gz
```

GitHub Actions (`.github/workflows/ci.yml`) runs the test suite on every push
and PR, builds the package, and attaches the artifacts to a release on `v*` tags.