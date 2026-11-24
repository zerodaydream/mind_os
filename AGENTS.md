# AGENTS.md

MindOS is a terminal-native personal operations environment. Read `docs/MISSION.md` for goals and
`docs/ARCHITECTURE.md` for design. Phase plans live in `docs/phases/`; decisions in `docs/adr/`.

## Commands

- Install: `uv sync`
- Database: `docker compose up -d db`, then `uv run mindos db migrate`
- Health check: `uv run mindos doctor`
- Gate (all must pass): `uv run ruff check src tests`, `uv run mypy`, `uv run lint-imports`, `uv run pytest`
- Live model test (calls the real API): `MINDOS_TEST_LIVE=1 uv run pytest -m live`

## Layout and rules

Source is in `src/mindos/`. Layers: `domain`, `application`, `agents`, `tools`, `integrations`,
`persistence`, `infra`, `presentation`, `bootstrap`, `sandbox`, `workers`.

- Dependencies point inward. `pyproject.toml` holds the import-linter contracts; keep them green.
- `domain` is pure: no I/O and no provider, database, or framework imports.
- Provider SDKs are imported only inside `integrations/`. Only `bootstrap/` wires adapters to ports.
- Agents are built with `deepagents.create_deep_agent`. Models see stub tools only; the engine runs every call. Never register a `HarnessProfile`, a filesystem or shell backend, or tools outside the allowlist.
- A tool that changes something outside the app sets a risk classifier from its validated arguments, a `bind_state` for any existing thing it edits or deletes, and is never retried after an ambiguous failure. Adapters report refusals as `ProviderError` and reconcile instead of repeating.
- LangSmith tracing is configured through `Settings` only (`MINDOS_LANGSMITH_*`). Never read tracing flags from the process environment, and never send unredacted payloads.
- Import from a package root (`mindos.domain`), not from its internal modules.
- No module-level mutable state. Pass dependencies in through constructors.
- Never log, print, or emit secrets. Secrets come from a `CredentialProvider`.
- Never load `.env` into the process environment. `load_settings()` parses it explicitly.
- Migrations are forward-only raw SQL in `persistence/migrations/versions/`.
- The `audit_events` table is append-only. Do not add code paths that update or delete it.
- Don't default to the smallest fix. Fix the root cause, even if the change is bigger.

## Conventions

- Python 3.12, strict mypy, ruff for lint and format (line length 100).
- Tests: unit tests need no services; integration tests use a temporary database on the compose
  Postgres and skip when it is down.
- Keep changes small and tied to the current phase file in `docs/phases/`.
