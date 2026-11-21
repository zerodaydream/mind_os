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
