# 01 · Spike Results

Run: 2026-11-22. Code: `spikes/` (throwaway, own `uv` project, own `.pgdata`). ADRs: `docs/adr/`.

| # | Spike | Result |
|---|---|---|
| 1 | Versions | **Pass** |
| 2 | DeepAgents lockdown | **Pass with conditions** (ADR 0001) |
| 3 | HITL resume across processes | **Pass** |
| 4 | Gemini function calling + thinking | **Pass** |
| 5 | Textual feasibility | **Pass with design change** (ADR 0002) |
| 6 | Claude Code reference capture | **Waived**: no `claude` CLI. UI spec built from public docs only; glyph/spacing details unverified, tuned later via theme tokens |
| 7 | Postgres + pgvector + pg_trgm | **Pass** (extensions load; HNSW tuning noted below) |

## 1 · Versions (resolved together by one `uv sync`)
`deepagents` 0.7.41 · `langchain` 1.5.2 · `langchain-core` 1.8.4 · `langgraph` 1.2.24 · `langgraph-checkpoint` 4.3.2 · `langgraph-checkpoint-postgres` 3.2.1 · `langchain-google-genai` 4.5.2 · `textual` 8.2.8 · `psycopg` 3.3.9 (+pool 3.3.3) · `msal` 1.39.0 · `keyring` 25.7.0. Python 3.12.

## 2 · DeepAgents lockdown
Defaults expose `delete, edit_file, execute, glob, grep, ls, read_file, write_file, task`. Default backend is in-memory (`StateBackend`): a forced `write_file` touched no disk, `execute` is unavailable. Built-in `excluded_tools` is documented as "not a security surface". Our own allowlist middleware (model view + execution boundary) rejected forced calls. See ADR 0001.

## 3 · HITL
`PostgresSaver` + `interrupt_on`. Process A paused at `send_email`; process B (new interpreter) resumed with `approve`; exactly 1 send; a second resume did not re-send. Thought signatures (content extras and `additional_kwargs`) survived, also under a restricted `allowed_msgpack_modules` serializer.

## 4 · Gemini (live API, models from `.env`)
- `gemini-3.5-flash-lite` profile levels: `minimal, low, medium, high`; default `minimal`.
- Tool loop with 2 parallel calls worked at `minimal`/`low`/`high` (1.5 s / 1.4 s / 3.0 s on a trivial task).
- Thought summaries appear for non-trivial prompts (`thinking` blocks); reasoning tokens reported in `usage_metadata.output_token_details.reasoning` (255 and 652 on the test prompt).
- Function-call thought signatures live in `additional_kwargs["__gemini_function_call_thought_signatures__"]`.
- A real signed `AIMessage` round-tripped through the strict serializer **identically**, and the API accepted the continuation. This confirms ARCHITECTURE §3.5.

## 5 · Textual
`Markdown.get_stream` exists. Widget-per-block degrades badly (544 ms flush at 5,000 blocks). A virtualized `ScrollView` stays at 25 ms at 100k lines. See ADR 0002.

## 7 · Postgres
`pg_trgm` GIN on 200k rows (33 MB table, 56 MB index): regex `PaymentFailed(Error|Exception)` used the index (`Bitmap Index Scan`), 48 ms vs 77 ms sequential. At this size the gain is small and the index is larger than the data, which supports the per-root promotion rule (§8.1). Test used PostgreSQL 14.19 (Homebrew); the target is 16+.

`pgvector/pgvector:pg16` (Docker 28.2.2): `CREATE EXTENSION vector` and `pg_trgm` both work; HNSW cosine index is used by the planner (`Index Scan using c_hnsw`), query 14 ms on 50k x 768-dim vectors. **Caveats:** (1) the build took 165 s because default `maintenance_work_mem` (64 MB) was exceeded after 17,700 tuples; the indexer must raise it for builds. (2) The data was uniform random vectors, the worst case for HNSW; the nearest neighbor of row 1 was not row 1 at `ef_search=40`, so **recall is unverified**. Measure recall on real embeddings in Phase 09, not here.

## Open
- **Spike 6 (waived):** if screenshots become available, drop them in `docs/ui-reference/` and add a visual-diff pass at the end of Phase 02.
- **Security:** the `.env` Gemini key worked, so it has **not** been rotated. Rotate it. Spike scripts read it without printing it, with LangSmith tracing off.
- **Env hygiene:** a `source .env` in a shell enabled LangSmith tracing and made a test hang on network calls. Phase 02 config loads settings explicitly and never exports `.env` into the shell.
