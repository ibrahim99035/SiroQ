# The database is Neon. There is no local Postgres service, and nothing here
# starts one; every target below runs against the connection strings in `.env`
# (see `.env.example`).

PY := .venv/bin/python
ALEMBIC := .venv/bin/alembic

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

# Runs outside Docker so a failure is a traceback you can read, and so the suite
# gets the Neon connection strings from `.env` rather than compose's environment.
test:
	$(PY) -m pytest -q

# alembic/env.py reads MIGRATIONS_DATABASE_URL from settings, which loads `.env`,
# so no extra wiring is needed. Use the direct (non-pooler) endpoint here: DDL
# must not run inside a pooled transaction.
migrate:
	$(ALEMBIC) upgrade head

makemigration:
	$(ALEMBIC) revision --autogenerate -m "$(name)"

shell:
	docker compose exec app bash

# BROKEN: scripts/seed.py is not in the repo.
seed:
	@echo "seed is not available: scripts/seed.py does not exist in this repo." && exit 1