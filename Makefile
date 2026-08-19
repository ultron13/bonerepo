.PHONY: test lint typecheck format dev dev-down test-int contracts

UV := uv run
COMPOSE := docker compose -f infrastructure/docker/docker-compose.yml

test:
	$(UV) pytest apps/api/tests/unit apps/worker/tests/unit apps/agent/tests/unit -v

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck:
	$(UV) mypy apps packages images

format:
	$(UV) ruff format .

dev:
	$(COMPOSE) build generator-image
	$(COMPOSE) up --build -d
	$(COMPOSE) exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
	$(COMPOSE) exec -T api uv run python -m plimsoll_api.seed
	@echo "Plimsoll is up. API http://localhost:8000  demo target http://localhost:8080"
	@echo "Sign in with admin@demo.plimsoll.dev / plimsoll-demo-password"

dev-down:
	$(COMPOSE) down -v

test-int:
	$(UV) pytest apps/api/tests/integration apps/worker/tests/integration -v -m integration

# Generation runs in a container, so the Docker-and-make-only promise holds.
# Both containers run as the invoking user, so the output is not root-owned.
contracts:
	$(COMPOSE) exec -T api uv run python -c \
	  "import json, plimsoll_api.main as m; print(json.dumps(m.app.openapi()))" \
	  > /tmp/plimsoll-openapi.json
	docker run --rm -u "$$(id -u):$$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
	  -v "$$PWD:/w" -v /tmp:/host-tmp -w /w node:20-alpine sh -c \
	  "mkdir -p packages/contracts/typescript/src && \
	   npx --yes openapi-typescript@7 /host-tmp/plimsoll-openapi.json \
	   -o packages/contracts/typescript/src/generated.ts"
