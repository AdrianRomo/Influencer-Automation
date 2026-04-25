# Dev helpers. Targets run against the docker-compose stack by default so
# contributors don't need a local Python + Postgres + Redis install.
#
# Override COMPOSE or PYTHON to run against something else, e.g.
#     make seed PYTHON="uv run python"

COMPOSE ?= docker compose
PYTHON  ?= $(COMPOSE) exec -T api python

.PHONY: help
help:
	@echo "Targets:"
	@echo "  up        — start the full stack"
	@echo "  down      — stop + remove containers"
	@echo "  logs      — tail api + worker logs"
	@echo "  migrate   — bootstrap + alembic upgrade head"
	@echo "  seed      — insert sample users + articles (idempotent)"
	@echo "  seed-fresh— wipe seeded rows then re-insert"
	@echo "  shell     — open a psql shell against the dev DB"
	@echo "  test      — run the pytest suite inside the api container"

.PHONY: up
up:
	$(COMPOSE) up -d --build

.PHONY: down
down:
	$(COMPOSE) down

.PHONY: logs
logs:
	$(COMPOSE) logs -f api worker

.PHONY: migrate
migrate:
	$(COMPOSE) exec api python /app/scripts/migrate_bootstrap.py
	$(COMPOSE) exec api alembic upgrade head

.PHONY: seed
seed: migrate
	$(PYTHON) /app/scripts/seed_dev.py

.PHONY: seed-fresh
seed-fresh: migrate
	$(PYTHON) /app/scripts/seed_dev.py --fresh

.PHONY: shell
shell:
	$(COMPOSE) exec db psql -U postgres -d mvp

.PHONY: test
test:
	$(COMPOSE) exec -T api pytest -v

.PHONY: openapi
openapi: ## Regenerate frontend/src/api-types.ts from the running API
	cd frontend && npm run openapi
