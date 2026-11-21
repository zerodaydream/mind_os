# AGENTS.md

opsagent is a terminal-native personal operations agent. Read `docs/MISSION.md` for goals and
`docs/ARCHITECTURE.md` for design. Phase plans live in `docs/phases/`; decisions in `docs/adr/`.

## Commands

- Install: `uv sync`
- Database: `docker compose up -d db`, then `uv run ops db migrate`
- Health check: `uv run ops doctor`
- Gate (all must pass): `uv run ruff check src tests`, `uv run mypy`, `uv run lint-imports`, `uv run pytest`
- Live model test (calls the real API): `OPS_TEST_LIVE=1 uv run pytest -m live`

## Layout and rules

Source is in `src/opsagent/`. Layers: `domain`, `application`, `agents`, `tools`, `integrations`,
`persistence`, `infra`, `presentation`, `bootstrap`, `sandbox`, `workers`.

- Dependencies point inward. `pyproject.toml` holds the import-linter contracts; keep them green.
- `domain` is pure: no I/O and no provider, database, or framework imports.
- Provider SDKs are imported only inside `integrations/`. Only `bootstrap/` wires adapters to ports.
- Import from a package root (`opsagent.domain`), not from its internal modules.
- No module-level mutable state. Pass dependencies in through constructors.
- Never log, print, or emit secrets. Secrets come from a `CredentialProvider`.
- Never load `.env` into the process environment. `load_settings()` parses it explicitly.
- Migrations are forward-only raw SQL in `persistence/migrations/versions/`.
- The `audit_events` table is append-only. Do not add code paths that update or delete it.

## Conventions

- Python 3.12, strict mypy, ruff for lint and format (line length 100).
- Tests: unit tests need no services; integration tests use a temporary database on the compose
  Postgres and skip when it is down.
- Keep changes small and tied to the current phase file in `docs/phases/`.
