# SiroQ Analysis Service

Deep-analysis service for application data files. Ingest files (CSV / Excel /
JSON) for an application in one request, and get back a persisted report:
column profiles, canonical-field classification, a 0–100 data-quality score,
and best-effort domain analytics — plus deterministic statistical forecasting
and live file preview/series endpoints.

- Consumer API guide: [`docs/API_INTEGRATION.md`](docs/API_INTEGRATION.md)
- Architecture & extensibility: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Adding features: [`docs/ADDING_FEATURES.md`](docs/ADDING_FEATURES.md)

## Run the service

### Windows / macOS / Linux (Docker)

**Prerequisites:** [Docker Desktop](https://www.docker.com/products/docker-desktop/)
installed and running, plus a Neon project with the `siroq_app` role created on
it. Only port `8000` needs to be free — the database is Neon, and nothing local
is started.

```bash
git clone https://github.com/ibrahim99035/SiroQ.git
cd SiroQ
cp .env.example .env                  # then fill in the Neon connection strings
docker compose up -d                  # builds the app image, starts the API
docker compose exec app alembic upgrade head   # creates the tables (first run only)
```

That is the whole setup. `docker compose up -d` starts **one** container — the API
on `:8000` — talking to Neon. Run
[`docker/init-db/01_create_app_role.sql`](docker/init-db/01_create_app_role.sql)
once against the branch database, as the branch owner, to create the `siroq_app`
role and its grants. It needs no PostGIS: the only extension the schema uses is
`pgcrypto`, and migration `001` installs it.

| URL | What |
|---|---|
| http://localhost:8000/dashboard/ | HTML dashboard |
| http://localhost:8000/docs | OpenAPI / Swagger UI |
| http://localhost:8000/health | Health check (`{"status":"ok","db":"up"}`) |

**Managing it**

```bash
docker compose logs -f app    # follow the app log
docker compose down            # stop the API
```

Raw uploads in development are written to `./data/storage`. The database is Neon,
so there is no local data volume to preserve.

### Without Docker (host Python)

Use this for development with live reload. Same Neon database — no local
Postgres, and none is needed.

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The database connection is read from `.env` (copy `.env.example` to `.env` and
edit it). `DATABASE_URL` and `MIGRATIONS_DATABASE_URL` have no defaults, so the
app names the missing variable rather than failing to connect. `STORAGE_PATH` is a
plain directory and is created on demand.

### What to expect on first run

- **The database starts empty.** There is no demo or seed data — the dashboard
  has nothing in it until you upload a file. The `make seed` target is currently
  broken (it calls `scripts/seed.py`, which is not in the repo).
- **Migrations are a separate step.** Nothing runs them at container start, which
  is why `alembic upgrade head` is the second command above. It is safe to re-run.
- **The dev API key ships in the source.** `API_KEY` defaults to
  `dev_api_key_change_in_production`; `ENVIRONMENT` defaults to `development`.
  `app/config.py` refuses to boot with that placeholder key when
  `ENVIRONMENT` is anything else, so set a real key before deploying anywhere.

### Published release

`v0.1.0` on GitHub ships a wheel, an sdist, and a container image at
`ghcr.io/ibrahim99035/siroq`. Note that `docker-compose.yml` **builds the app
image locally** rather than pulling from GHCR, so a first run installs the Python
dependencies onto your machine.

## Test

```bash
make test        # or: .venv/bin/python -m pytest -q
```

Reads the Neon connection strings from `.env`. The suite derives a throwaway
`*_test` database from whichever database it is given, so it can never run
against `neondb` itself, and it pins `STORAGE_DRIVER=local` before importing any
application code — tests wipe storage between cases, so pointing them at the
bucket would delete real objects.

## Build the package

```bash
.venv/bin/python -m build     # -> dist/*.whl + dist/*.tar.gz
```

GitHub Actions (`.github/workflows/ci.yml`) runs the test suite on every push
and PR, builds the package, and attaches the artifacts to a release on `v*` tags.