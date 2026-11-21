# 01 · Spikes (verify before building)

Time-box: 1–2 days. Output: ADRs in `docs/adr/`. Throwaway code only.

## Skills
| Skill | Source | Use |
|---|---|---|
| `langgraph-fundamentals`, `langgraph-persistence` | `npx skills add langchain-ai/langchain-skills@…` (19K installs) | Checkpointer, thread_id |
| `langchain-build-agent` | local `.agents/skills` | Agent wiring |
| `idea-validator`, `codebase-modernizer` | local | Not needed |
| `dev-machine-setup` | local | Postgres, uv, ripgrep |

## Spikes
1. **Versions.** Pin Python (3.12 per `.python-version`; deepagents needs ≥3.11), `deepagents`, `langchain`, `langgraph`, `langgraph-checkpoint-postgres`, `langchain-google-genai`, `textual`, `psycopg`, `msal`. Run one `uv sync` that resolves.
2. **DeepAgents lockdown.** Can built-in fs/shell/execute tools be fully removed? Assert tool list == ours. If no → drop DeepAgents, use plain LangGraph agent. ADR.
3. **HITL round-trip.** `interrupt_on` + `PostgresSaver`, kill process mid-interrupt, resume in new process with `Command(resume=…)`.
4. **Gemini function-calling.** id/name/count matching; streaming + tool calls.
5. **Textual feasibility.** Streaming Markdown at 30–60 Hz, multiline `TextArea` prompt with `/` and `@` completion, 5k-line transcript scrolling. Measure CPU.
6. **Claude Code reference capture.** Run `claude` locally; screenshot: welcome, spinner, tool row, expanded result, permission dialog, todo list, slash menu, status line. Save to `docs/ui-reference/` (local only, not committed if licensing unclear).
7. **Postgres.** `pgvector` + `pg_trgm` install on this Mac.

## Gate
- ADRs written for 2, 3, 5. `uv sync` reproducible.
