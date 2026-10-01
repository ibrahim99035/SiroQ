up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

migrate:
	docker compose exec app alembic upgrade head

makemigration:
	docker compose exec app alembic revision --autogenerate -m "$(name)"

# BROKEN: scripts/seed.py is not in the repo.
seed:
	@echo "seed is not available: scripts/seed.py does not exist in this repo." && exit 1

test:
	docker compose exec app pytest -v

shell:
	docker compose exec app bash

psql:
	docker compose exec db psql -U siroq -d siroq