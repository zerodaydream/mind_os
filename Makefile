.PHONY: install sync sync-cli tool tool-global sync-agents sync-voice tool-agents tool-voice test lint up down migrate teardown

UV := $(shell command -v uv 2>/dev/null)
ifeq ($(UV),)
  UV := $(HOME)/.pyenv/shims/uv
endif

CLI_EXTRA := --extra cli

install: sync tool-global

sync sync-cli:
	$(UV) sync --group dev $(CLI_EXTRA)

# Repo .venv + ~/.local/bin/mindos (any terminal)
tool tool-global: sync
	$(UV) tool install -e ".[cli]" --force

sync-agents:
	$(UV) sync --group dev --extra agents

sync-voice:
	$(UV) sync --group dev --extra voice

tool-agents: sync
	$(UV) tool install -e ".[cli]" --force

tool-voice: tool-global

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
