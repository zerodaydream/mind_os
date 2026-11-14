.PHONY: install sync sync-agents test lint up down migrate teardown

UV := $(shell command -v uv 2>/dev/null)
ifeq ($(UV),)
  UV := $(HOME)/.pyenv/shims/uv
endif

install: sync

sync:
	$(UV) sync --group dev

sync-agents:
	$(UV) sync --group dev --extra agents

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check backend cli tests

up:
	docker compose --profile lean up -d

down:
	docker compose --profile lean down

migrate:
	$(UV) run alembic upgrade head

teardown:
	docker compose --profile lean down -v
