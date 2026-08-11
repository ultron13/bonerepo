.PHONY: test lint typecheck format dev dev-down test-int

UV := uv run
COMPOSE := docker compose -f infrastructure/docker/docker-compose.yml

test:
	$(UV) pytest apps/api/tests/unit -v

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck:
	$(UV) mypy apps packages images

format:
	$(UV) ruff format .

dev:
	$(COMPOSE) up --build -d
	$(COMPOSE) exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
	$(COMPOSE) exec -T api uv run python -m plimsoll_api.seed
	@echo "Plimsoll is up. API http://localhost:8000  demo target http://localhost:8080"
	@echo "Sign in with admin@demo.plimsoll.dev / plimsoll-demo-password"

dev-down:
	$(COMPOSE) down -v

test-int:
	$(UV) pytest apps/api/tests/integration -v -m integration
