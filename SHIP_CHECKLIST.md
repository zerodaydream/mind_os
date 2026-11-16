# Ship checklist

- [ ] `uv sync --group dev` (add `--extra agents` if needed)
- [ ] `uv run alembic upgrade head` against Docker Postgres (`5433`)
- [ ] `make test` green
- [ ] `backend/config.json` services match deployment
- [ ] `.env`: `MINDOS_SECRET_KEY`, `DATABASE_URL`, LLM/OAuth vars
- [ ] Worker running (`uv run backend-worker`) for outbox + Gmail schedule
- [ ] Smoke: `GET /health`, ingest, `GET /v1/search`, commitments/tensions
- [ ] Optional: Google OAuth + `POST /v1/integrations/google/sync`
- [ ] Optional: `POST /v1/agents/run` with `graph` + `agents` + `llm` enabled
