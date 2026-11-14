# Quickstart

Local-only MVP. Consumer ChatGPT/Claude subscriptions do **not** include API billing.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) (Python + deps)
- Python 3.12+ (uv installs via `.python-version` if missing)
- Docker (for Postgres + api + worker)
- **Lean:** 8 GB RAM, ~25 GB disk

## 1. Environment

```bash
cp .env.example .env
cp backend/config.json.example backend/config.json
```

| Variable | Meaning |
|----------|---------|
| `MINDOS_LLM_MODE=degraded` | No paid LLM calls (default) |
| `MINDOS_LLM_MODE=mock` | Tests / deterministic responses |
| `MINDOS_LLM_MODE=live` | Paid APIs — requires real `MINDOS_SECRET_KEY` |
| `DATABASE_URL` | Async Postgres URL (`postgresql+asyncpg://…`) |

No API keys required for ingest/search/rules once those features land; today: health + tests work without keys.

## 2. Native dev (fastest loop)

```bash
uv sync --group dev
make test
uv run uvicorn backend.app:app --reload --port 8000
```

Optional extras: `uv sync --group dev --extra llm --extra agents`

Check: `curl http://localhost:8000/health` → `{"status":"ok"}`

### Terminal (primary UX)

From any terminal, prefer the repo launcher (uses `.venv`, agents extra when synced):

```bash
export PATH="/path/to/mind_os/bin:$PATH"   # once per shell, or add to ~/.zshrc
mindos chat
```

Or from the repo root: `.venv/bin/mindos chat` or `make sync-agents && uv run mindos chat`.

A bare `mindos` on PATH from an old/global install will **not** see `deepagents`.

Gmail OAuth: open `http://127.0.0.1:8000/v1/integrations/google/start` once (API must be running).

Optional agents (Deep Agents for plan/execute): `uv sync --group dev --extra agents`

## 3. Docker (full lean stack)

```bash
cp .env.example .env
docker compose --profile lean up -d --build
# Migrations on the host (Postgres is on localhost:5433):
uv run alembic upgrade head
```

Chat history needs migration `0002` (`chat_sessions`). Use menu **status** to verify.

**Docker logs:**

```bash
docker compose --profile lean logs -f api worker
docker compose --profile lean logs postgres --tail 100
```

Terminal `mindos` runs on the **host** and uses `DATABASE_URL=...@localhost:5433/...` — not inside `api`.

| Profile | Services |
|---------|----------|
| `lean` | postgres, api, worker |
| `full` | + langfuse (optional; skip on 8 GB RAM) |
| `optional-llm` | + ollama |

```bash
make down        # stop
make teardown    # stop + remove volumes
```

## 4. Budget / LLM (when `live`)

Defaults in `.env`: `MINDOS_LLM_DAILY_USD=2.00`, `MINDOS_LLM_MONTHLY_USD=40.00`, `MINDOS_LLM_KILL_SWITCH=0`. Embeddings planned as **local** (sentence-transformers); not billed via API.

## 5. Troubleshooting

| Issue | Fix |
|-------|-----|
| `live` mode fails on startup | Set `MINDOS_SECRET_KEY` to a non-`change-me` value |
| `role "mindos" does not exist` on migrate | Host `alembic` hit **local** Postgres on 5432, not Docker — use `DATABASE_URL=...@localhost:5433/mindos` (compose maps **5433→5432**) |
| Port 5432 in use | Default Docker publish is **5433** on the host (see `docker-compose.yml`) |
| Tests flaky on env | `get_settings()` is cached; tests clear cache in `conftest.py` |
