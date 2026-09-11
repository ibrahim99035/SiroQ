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

seed:
	docker compose exec app python scripts/seed.py

test:
	docker compose exec app pytest -v

shell:
	docker compose exec app bash

psql:
	docker compose exec db psql -U siroq -d siroq