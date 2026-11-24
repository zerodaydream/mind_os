# ADR 0001: Keep DeepAgents behind `AgentRuntime`, with a fail-closed tool allowlist

Status: Accepted (Phase 01 spike 2, `spikes/s02/`)
Date: 2026-11-22
Versions tested: `deepagents` 0.7.41, `langchain` 1.5.2, `langgraph` 1.2.24

## Context
ARCHITECTURE §14 forbids framework filesystem/shell tools. Open question (§17.3): can DeepAgents' built-ins be removed?

## Findings
1. Default effective tool set with one custom tool: `calendar_search`, `delete`, `edit_file`, `execute`, `glob`, `grep`, `ls`, `read_file`, `task`, `write_file`. The model sees all of them.
2. Default backend is `StateBackend` (in-memory virtual files). A model call to `write_file` succeeds against virtual state and did **not** touch disk. `execute` returned "Execution not available" because the backend has no command execution.
3. Profile option `excluded_tools` (`_ToolExclusionMiddleware`) hides tools from the model and rejects calls, but its own docstring states: *"it is not a security surface"* and the executor still has the tools registered.
4. A small custom middleware (`wrap_model_call` filters `request.tools` to an allowlist; `wrap_tool_call` rejects any other tool name) hid all built-ins from the model and returned `REJECTED` for forced calls to `write_file` and `execute`. Canary file was not created.

## Decision
- Keep DeepAgents as one adapter behind `AgentRuntime` (ARCHITECTURE §3.0). The plain LangGraph adapter stays the fallback.
- Always use `StateBackend`. Never configure `FilesystemBackend` or any shell/sandbox backend.
- Install our own `AllowlistMiddleware` (model view **and** execution boundary) on the orchestrator **and every subagent**. Do not rely on `excluded_tools`.
- Disable the built-in general-purpose subagent (`task` is not on the allowlist except through our `delegate`).
- CI test: for each agent, assert the model-visible tool set equals the allowlist, and that a forced call to each built-in name is rejected with no side effect.

## Consequences
- Pinning DeepAgents is mandatory; re-run the lockdown test on every upgrade.
- If a future version stops honoring the middleware order, the test fails and we switch to the fallback adapter.

## Open
- Verify the allowlist also covers subagents created through our own `delegate` (Phase 03).

## Update (Phase 04, then ADR 0007)
Phase 04 shipped the plain LangChain adapter (ADR 0003). ADR 0007 returns to Deep Agents and keeps the allowlist invariant above, tested on the real harness. The plain adapter is no longer a fallback in the tree; reverting the pin and two call sites restores it.
