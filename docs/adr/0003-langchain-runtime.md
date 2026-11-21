# ADR 0003: Phase 04 uses the plain LangChain agent adapter, not DeepAgents

Status: Accepted (Phase 04)
Date: 2026-11-22
Versions: `langchain` 1.5.2, `langgraph` 1.2.24 (DeepAgents is not a dependency)

## Context
ADR 0001 kept DeepAgents behind `AgentRuntime` and named the plain LangGraph agent as the fallback adapter. It required an allowlist middleware on every agent because DeepAgents ships filesystem, shell, and `task` tools that cannot be removed safely.

Phase 04 needs three orchestrator tools (`delegate`, `write_todos`, `read_artifact`), one read-only subagent, our own engine pipeline, and checkpointed threads. DeepAgents' main additions (virtual filesystem, built-in subagents, its own todo and summarization middleware) are things we either forbid or implement ourselves.

## Decision
- Use `langchain.agents.create_agent` as the `AgentRuntime` adapter (`agents/runtime.py`). It is the same graph DeepAgents builds on.
- Models see **stub tools**: our JSON schemas, no executable body. One middleware (`agents/bridge.py`) intercepts every tool call and sends it to `ToolExecutionEngine.execute`. The engine, not the framework, validates, authorizes, classifies, runs, normalizes, and audits.
- The same middleware filters the model-visible tool list to the agent's grant, so the visible set equals the allowlist. This keeps the ADR 0001 invariant without a framework tool set to defend. A test asserts it for the orchestrator and the subagent.
- Subagents are separate compiled graphs, invoked inside the `delegate` tool, with `checkpointer=False` so they never inherit the orchestrator's thread. Only a `SubagentResult` built by code returns. Evidence comes from engine results, not from model claims.
- `delegate` is not granted to any subagent, so depth is one.
- DeepAgents stays a possible adapter. Revisit it only if a phase needs a capability we would otherwise rebuild (for example summarization of long threads, Phase 10).

## Consequences
- No `deepagents` dependency or pin to track. ADR 0001's lockdown test becomes the allowlist test in `tests/unit/test_agent_runtime.py`.
- We own the plan tool and its UI event (`PlanUpdated`).
- Streaming relies on LangGraph `stream_mode=["messages", "updates"]`. Nested subagent tokens are excluded by the checkpoint namespace filter plus `checkpointer=False`; a test checks that subagent thoughts never reach the event stream.
- A step limit, deadline, or cancel can leave a tool call without a response in the checkpoint. The runtime repairs the thread before each run so providers do not reject the history.
