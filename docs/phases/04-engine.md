# 04 · Tool Engine + First Subagent

Maps to ARCHITECTURE §3, §4.1–4.3, §9.1 (checkpoints). First real agent wired to the TUI.

## Skills
| Skill | Source |
|---|---|
| `tool-design` | local |
| `multi-agent-patterns` | local |
| `langgraph-fundamentals`, `langgraph-persistence` | `langchain-ai/langchain-skills` (19K) |
| `langchain-build-agent`, `langchain-agent-tracing` | local |
| `context-optimization`, `context-compression` | local → artifacts, token budget |
| `python-testing-patterns` | `wshobson/agents` |

## Build
- `ToolSpec` registry; duplicate names fail startup.
- `ToolExecutionEngine.execute()` pipeline: validate → permission → risk classify → policy stub (allow R0) → execute → normalize → audit.
- Result normalization: size bound, artifact offload (`read_artifact`), untrusted-text tagging, redaction.
- `application` adapter: LangGraph stream → app events (`ToolCall*`, `PlanUpdated`, `Subagent*`, usage).
- Orchestrator (≤10 tools) + **Calendar subagent, read-only** (Google Calendar stub/fixture until Phase 06).
- `TaskBrief` / `SubagentResult` contracts; evidence required for side-effect claims.
- Limits: steps, depth=1, deadline, token and tool budgets.
- Provider usage table + cost shown in status line.

## Reasoning
- Per-role effort wiring: orchestrator (plan, verify) high; subagent steps low or medium; utility minimal.
- Stream thought summaries as `ThinkingDelta`; never persist thought text in audit bodies.
- Active tool-loop messages are never compacted (signatures).
- Eval each role at 2+ levels; set defaults from pass rate and cost.

## Gate
- Forbidden-import test: only engine reaches adapters.
- Fake-LLM plumbing evals pass; one real-model eval scenario recorded.
- Tool validation, output-bound, idempotency tests.

## Don't
- No framework fs/shell tools exposed. No subagent-spawns-subagent.

## Status (built 2026-11-22)
Built and passing the gate. Decision records: `docs/adr/0003-langchain-runtime.md` (stub tools and one engine bridge; its plain-LangChain runtime choice is superseded by `docs/adr/0007-deep-agents-runtime-and-langsmith-tracing.md`, which moved the runtime to Deep Agents).

Done:
- **Engine** (`tools/`): `ToolSpec` registry (duplicate or malformed names fail startup, grants naming unknown tools fail startup); `ToolExecutionEngine.execute` runs lookup, per-agent grant, run budget, validation, risk, policy, execute (timeout, jittered retry for read tools only), normalize, audit. Every refusal is a structured result the model can read. `ReadOnlyPolicy` allows R0 and denies the rest (fail closed until Phase 05).
- **Normalization**: secrets redacted, oversized results offloaded to `artifacts` and read back with `read_artifact`, untrusted output wrapped in `<untrusted>` tags with forged tags defused, evidence collected from raw results.
- **Calendar namespace, read-only**: `calendar.search_events`, `calendar.get_event`. Times are resolved and rendered in code (zone, offset, weekday). Backed by `FixtureCalendar` (sample week) until Phase 06.
- **Agents** (`agents/`): orchestrator with `delegate`, `write_todos`, `read_artifact`; calendar subagent; `TaskBrief` in, code-built `SubagentResult` out; depth 1; limits for steps, tool calls, deadline, parallel subagents. Hitting a limit ends the run as a partial result with a notice, and the next turn repairs dangling tool calls.
- **Events**: the runtime emits `ThinkingDelta/Complete`, `AssistantText*`, `PlanUpdated`, `Subagent*`, `ToolCall*`, `UsageUpdated` (session totals, cost when `OPS_PRICE_*` are set). `/think` now reaches the orchestrator through `SubmitMessage.effort`. Follow-up messages reuse the conversation thread, so history survives.
- **Persistence**: migration `0002` (`artifacts`), `ArtifactRepository`, `UsageRepository` (`provider_usage` incl. reasoning tokens), async checkpointer with a restricted deserializer (`open_checkpointer`).
- **Contracts**: `tools` depend on the domain only and are framework-free; a test fails if anything but the engine calls a tool handler.

Gate results: 170 tests (unit, UI, integration on Postgres, architecture), `ruff`, `mypy`, 9 import contracts. Fake-LLM plumbing evals cover delegation, allowlists, forced calls, budgets, step limit, deadline, cancel, usage, history, and a full run on the real checkpointer.

Not done, with owner:
- **Real-model eval scenario**: `tests/unit/test_live_agent.py` is written and skipped unless `OPS_TEST_LIVE=1`. The Gemini key in `.env` returned `403 PERMISSION_DENIED` for every configured model on 2026-11-22, so no live run was recorded. Replace the key, run `OPS_TEST_LIVE=1 uv run pytest -m live`, and record the result here. Per-role effort evals at two or more levels wait on the same thing.
- **`tool_executions` table, approvals, resume, startup reconciliation**: Phase 05. `AgentRuntime.resume` raises until then.
- **Google Calendar adapter and keychain credentials**: Phase 06. `ask_user` and `search_tools`: later phases.
- **Context percentage and per-model pricing in the status line**: `UsageUpdated.context_pct` is unset; one price pair applies to all roles.

## Update 2026-11-25 (ADR 0007)
The runtime moved from `create_agent` to `deepagents.create_deep_agent`. The engine, stub tools,
bridge, allowlist, and every test in this phase are unchanged and pass on the new harness. Our
`_repair_thread` was deleted: Deep Agents' `PatchToolCallsMiddleware` does it. LangSmith tracing was
added behind a `Tracing` port (off by default, redacted, one thread per conversation).
Not verified: a live Gemini run through the Deep Agents harness (its summarization middleware is on
by default), and a live LangSmith trace.
