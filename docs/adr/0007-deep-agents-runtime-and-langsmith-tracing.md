# ADR 0007: Deep Agents is the agent runtime; LangSmith traces it, behind our own switch

Status: Accepted. Supersedes the runtime choice of ADR 0003 and completes ADR 0001.
Date: 2026-11-25
Versions: `deepagents` 0.7.41 (pinned), `langchain` 1.5.2, `langgraph` 1.2.24, `langsmith` 0.16.2

## Context
ADR 0003 used `langchain.agents.create_agent` because Deep Agents' extras (virtual filesystem,
built-in subagents, shell) were things we forbid. The project's agent guidance now makes Deep Agents
the default builder for reasoning agents, and `create_deep_agent` is the same `create_agent` plus a
harness we would otherwise have to build: thread summarization, repair of dangling tool calls,
prompt caching, and refusal handling. ADR 0001 already proved the harness can be locked down.

## Decision

### Runtime
- Orchestrator and every subagent are built with `deepagents.create_deep_agent`
  (`agents/runtime.py`, `agents/subagents.py`), still behind the `AgentRuntime` port.
- **The engine stays the only authority.** Models see stub tools carrying our schemas. One
  middleware (`agents/bridge.py`) hides every tool outside the agent's grant and sends every call to
  `ToolExecutionEngine.execute`. Approval pause and resume, deferred subagent approvals, taint,
  auth errors, and audit are unchanged (ADR 0004, 0005, 0006).
- **Lockdown (ADR 0001) is kept and tested** on the real harness: the model-visible set equals the
  allowlist, and forced calls to `write_file`, `execute`, `task`, `ls`, `glob`, `grep` are rejected
  with no effect and an audit row. We do not register a `HarnessProfile` or use `excluded_tools`:
  the profile registry is global state, and ADR 0001 found `excluded_tools` is not a security
  surface. `StateBackend` (the default, in-memory) is the only backend; no filesystem or shell
  backend is ever configured.
- Deep Agents' own `task` tool and general-purpose subagent exist in the graph but are unreachable:
  the allowlist hides `task`, and the engine would refuse it. Delegation is our `delegate` tool.
- **Removed code.** Deep Agents' `PatchToolCallsMiddleware` answers tool calls left dangling by a
  stop or cancel, so our `_repair_thread` is deleted. Its test still passes.
- **Kept ours:** `write_todos` (it emits `PlanUpdated`), `delegate`, `read_artifact`, approvals,
  policy, and the whole execution engine. Deep Agents provides none of them.
- Deep Agents is pre-1.0: the version is pinned, and the lockdown tests run on every upgrade. If a
  release breaks them, revert the pin; the call sites are two lines.

### Tracing
- `Tracing` is a domain port with one adapter, `integrations/tracing/langsmith.py`. LangChain,
  LangGraph, and Deep Agents trace themselves inside a `tracing_context`; the adapter decides
  whether, where, and what.
- **Off by default, and only our settings turn it on.** `OPS_LANGSMITH_TRACING=true` enables it. A
  `LANGSMITH_TRACING=true` in the shell does not: when our flag is off the adapter opens
  `tracing_context(enabled=False)`, which wins. (A `source .env` once turned tracing on silently; see
  `docs/phases/01-spikes-results.md`.) Reason: runs carry mail and calendar content.
- **Redaction before send.** The client's input and output hooks run `redact` (secrets masked).
  `OPS_LANGSMITH_HIDE_IO=true` sends run structure only. Sampling is `OPS_LANGSMITH_SAMPLING_RATE`.
- **One run, one trace, one thread.** Each run (and each resume of it) opens one context with
  `thread_id`, `conversation_id`, `opsagent_run_id`, and version metadata. Metadata is not copied to
  child runs by LangSmith, so the orchestrator and every subagent invocation carry `thread_id`
  explicitly. Subagent runs are named `opsagent.subagent.<name>`, the orchestrator
  `opsagent.orchestrator`.
- **Destination.** `OPS_LANGSMITH_AGENT_ID` plus `OPS_LANGSMITH_ENVIRONMENT` address an agent in an
  agent-based workspace; otherwise `OPS_LANGSMITH_PROJECT`. Set `OPS_LANGSMITH_ENDPOINT` for a
  non-US or self-hosted instance and `OPS_LANGSMITH_WORKSPACE_ID` if the key reaches several.
- **Key.** Keychain first (`ops auth langsmith`), then `OPS_LANGSMITH_API_KEY` /
  `LANGSMITH_API_KEY` in `.env`. Never logged. Traces are flushed on exit.

## Consequences
- `deepagents` joins `pyproject.toml` pinned; `langchain-core` and `langsmith` rose one patch level.
- Summarization is now on by default through the harness. Long threads are compacted for the model;
  the checkpoint keeps what LangGraph keeps. Verify it against Gemini's context limits in a live run.
- Not verified here: a live trace in a LangSmith workspace, and a live Gemini run through the
  Deep Agents harness. Tests use scripted models and an in-memory trace recorder.
- Metrics and evals on these traces are future work; thread IDs and stable metadata are in place for it.
