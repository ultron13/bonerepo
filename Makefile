.PHONY: test lint typecheck format dev dev-down test-int e2e contracts backup restore-drill

UV := uv run
COMPOSE := docker compose -f infrastructure/docker/docker-compose.yml
# The web checks run in a container for the same reason the build does: the
# promise is Docker and make, not a matching local Node.
# Runs as the invoking user, so nothing it writes lands root-owned in the
# tree -- the same reason the contracts target does it.
WEB := docker run --rm -u "$$(id -u):$$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
  -v "$$PWD/apps/web:/srv" -w /srv node:20-alpine npm
# Playwright needs a browser and its libraries, which the plain node image
# does not carry, and it reaches the stack on the host rather than a
# compose network.
WEB_E2E := docker run --rm --network host -u "$$(id -u):$$(id -g)" -e HOME=/tmp \
  -v "$$PWD/apps/web:/srv" -w /srv \
  -e PLIMSOLL_WEB_URL=http://localhost:3000 \
  mcr.microsoft.com/playwright:v1.62.1-noble npx playwright test

test:
	$(UV) pytest apps/api/tests/unit apps/worker/tests/unit apps/agent/tests/unit \
	  packages/executor-sdk/tests packages/contracts/python/tests -v

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(WEB) run lint

typecheck:
	$(UV) mypy apps packages images
	$(WEB) run typecheck

format:
	$(UV) ruff format .

dev:
	$(COMPOSE) build generator-image
	$(COMPOSE) up --build -d
	$(COMPOSE) exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
	$(COMPOSE) exec -T api uv run python -m plimsoll_api.seed
	@echo "Plimsoll is up. Web http://localhost:3000  API http://localhost:8000  demo target http://localhost:8080"
	@echo "Sign in with admin@demo.plimsoll.dev / plimsoll-demo-password"

dev-down:
	$(COMPOSE) down -v

test-int:
	$(UV) pytest apps/api/tests/integration apps/worker/tests/integration -v -m integration

# The seam the API tests cannot reach: what a person sees. Requires `make dev`
# and a browser, so it runs in a container like everything else.
e2e:
	$(WEB_E2E)

backup:
	./scripts/backup.sh $(DEST)

# An untested backup is not a backup. This restores the most recent one into a
# scratch database and compares it against the live one, so the procedure is
# exercised on a schedule rather than first attempted during an incident.
restore-drill:
	./scripts/restore-drill.sh

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
