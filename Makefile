COMPOSE ?= docker compose

# Loaded so `make psql` knows the credentials. Absent .env is not an error —
# `make lock` and `make up` still work, and `cp .env.example .env` is step one.
-include .env
export

.PHONY: up down migrate seed psql logs lock revision reset test test-up sweep

up:                ## Build and start db, redis and app
	$(COMPOSE) up -d --build

down:              ## Stop everything, keeping the pgdata volume
	$(COMPOSE) down

migrate:           ## Apply migrations to head
	$(COMPOSE) exec app alembic upgrade head

seed:              ## Load reference data (idempotent, safe to re-run)
	$(COMPOSE) exec app python -m app.seeds

psql:              ## Interactive psql shell
	$(COMPOSE) exec db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

logs:              ## Follow all service logs
	$(COMPOSE) logs -f

lock:              ## Regenerate Pipfile.lock (host has no pipenv, so use a container)
	docker run --rm -v "$(CURDIR)":/w -w /w python:3.12-slim \
		sh -c "pip install --no-cache-dir pipenv && pipenv lock"

revision:          ## Autogenerate a migration: make revision m="add thing"
	$(COMPOSE) exec app alembic revision --autogenerate -m "$(m)"

reset:             ## DESTRUCTIVE: stop and delete the pgdata volume
	$(COMPOSE) down -v

sweep:             ## Release expired stock reservations (run on demand or from cron)
	$(COMPOSE) exec -T app python -m app.jobs.sweep_reservations

test-up:           ## Start db-test and app, blocking until healthy
	$(COMPOSE) up -d --wait db-test app

test: test-up      ## Run the test suite against db-test
	$(COMPOSE) exec -T app pytest
