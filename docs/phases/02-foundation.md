# 02 · Foundation

Maps to ARCHITECTURE §2, §10, §13.2, §15.

## Skills
| Skill | Source |
|---|---|
| `python-project-structure` | `wshobson/agents` (15.7K) |
| `postgresql-table-design` | `wshobson/agents` (29K) |
| `python-testing-patterns` | `wshobson/agents` (41K) |
| `agent-config` | local → rewrite `AGENTS.md` |
| `devops-pipeline` | local → pre-commit + lean CI |
| `cli-builder` | local → `mindos` CLI |
| `security-setup` | local → secret scan hooks |

## Build
- `pyproject.toml` (uv), `src/mindos/` layers per README §4, `bootstrap/`, `cli.py` (`mindos`, `mindos doctor`, `mindos db migrate`).
- `import-linter` contracts (§2.3) green from day one; CI fails on violation.
- `infra/`: pydantic-settings config, structlog, contextvars correlation IDs, clock, ids.
- `persistence/`: psycopg pool, Alembic baseline (`conversations`, `messages`, `agent_runs`, `audit_events` insert-only, `integration_states`), LangGraph `setup()`.
- `domain/`: ports (`LLMProvider`, `AgentRuntime`, `CredentialProvider`), risk enums, event models (**with fixes from 00-verdict #3**).
- `application/`: in-process event bus, facade stub (`SubmitMessage`, `Cancel`).
- LLM port + Gemini adapter (model IDs from config only). Port takes one `reasoning_effort`; adapter maps to `thinking_level` (Gemini 3+) or `thinking_budget` (2.5), reads the model profile, falls back to the nearest lower level, replays original messages so thought signatures survive (ARCHITECTURE §3.5).
- Config: `MINDOS_REASONING_*`, `MINDOS_INCLUDE_THOUGHTS`. `provider_usage` gets a `reasoning_tokens` column.
- `docker-compose.yml` for Postgres+pgvector; `.env.example`.
- Replace root `AGENTS.md` (remove Astryx block; point to MISSION/ARCHITECTURE).

## Gate
- `lint-imports`, `ruff`, `mypy`, `pytest` green.
- `mindos doctor` reports DB + extensions OK.
- Audit table rejects UPDATE/DELETE for app role (test).

## Don't
- No module-level mutable state. No provider imports outside `integrations/`.
