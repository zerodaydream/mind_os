"""The only link between a LangChain agent and the tool engine.

The model is shown stub tools that carry our JSON schemas but cannot run. Every call is taken
over in `awrap_tool_call` and sent through `ToolExecutionEngine.execute`, so the engine is the
single path to an adapter (ARCHITECTURE section 4.1) and validation, permission, risk, policy,
normalization, and audit are ours, not the framework's. `awrap_model_call` also hides every tool
the agent has no grant for, so the model-visible set always equals the allowlist (ADR 0001, 0003).

Approval flow: when the engine returns `status="pending_approval"`, the bridge calls
`langgraph.types.interrupt()`, which pauses the graph at its checkpoint. This is transport only.
LangGraph re-runs this whole function from the top on resume, so everything before the interrupt
is idempotent: the engine returns the same pending approval for a replay without announcing it
again. After the interrupt returns, the bridge always goes back through the engine, which reads
the approval's real state from the database. What the UI passed in the resume value is used only
for the rejection message; it can never cause an execution.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from langgraph.types import interrupt as lg_interrupt

from opsagent.agents.hub import RunHub
from opsagent.domain import ToolInvocation, ToolResult, current, events, redact
from opsagent.tools import ToolExecutionEngine, model_schema, preview

log = logging.getLogger(__name__)


def stub_tools(engine: ToolExecutionEngine, agent: str) -> list[StructuredTool]:
    """Tools shaped like ours that the framework can bind but never run."""

    async def never(**_: Any) -> str:
        raise RuntimeError("tools run only through the engine")  # pragma: no cover

    return [
        StructuredTool(
            name=spec.name,
            description=spec.summary,
            args_schema=model_schema(spec.input_model),
            coroutine=never,
        )
        for spec in (engine.spec(n) for n in sorted(engine.granted(agent)))
    ]


class EngineMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        engine: ToolExecutionEngine,
        hub: RunHub,
        agent: str,
        system_prompt: Callable[[], str],
        can_pause: bool,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._hub = hub
        self._agent = agent
        self._system_prompt = system_prompt
        self._can_pause = can_pause  # only a checkpointed graph can wait for a human

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> Any:
        allowed = self._engine.granted(self._agent)
        visible = [t for t in request.tools if getattr(t, "name", None) in allowed]
        return await handler(
            request.override(
                tools=visible, system_message=SystemMessage(content=self._system_prompt())
            )
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        ids = current()
        run_id, subagent_id = ids.get("run_id"), ids.get("subagent_id")
        state = self._hub.get(run_id)
        emit = state.emit if state else (lambda _event: None)
        args: dict[str, Any] = call.get("args") or {}
        emit(
            events.ToolCallStarted(
                run_id=run_id,
                tool_call_id=call["id"] or "",
                tool=call["name"],
                args_preview=preview(json.dumps(redact(args), default=str), 120),
                subagent_id=subagent_id,
            )
        )
        tool_call_id = call["id"] or ""
        admit = state.budget.admit if state else None

        def invocation(approval_id: str | None = None) -> ToolInvocation:
            return ToolInvocation(
                tool=call["name"],
                args=args,
                agent=self._agent,
                tool_call_id=tool_call_id,
                run_id=run_id,
                approval_id=approval_id,
                can_pause=self._can_pause,
            )

        result = await self._engine.execute(invocation(), emit=emit, admit=admit)
        if result.status == "pending_approval" and result.approval_id is not None:
            decision = lg_interrupt(
                {
                    "approval_id": result.approval_id,
                    "tool": call["name"],
                    "run_id": run_id,
                    "conversation_id": ids.get("conversation_id"),
                    "thread_id": ids.get("thread_id"),
                }
            )
            comment = decision.get("comment") if isinstance(decision, dict) else None
            result = await self._engine.execute(invocation(result.approval_id), emit=emit)
            if result.error_code == "approval_rejected":
                reason = comment or "no reason given"
                result = result.model_copy(
                    update={"content": f"[rejected by user: {reason}; not executed]"}
                )
        if state and subagent_id:
            state.ledger(subagent_id).add(result)
        emit(_finished(result, run_id))
        return ToolMessage(
            content=result.content,
            tool_call_id=tool_call_id,
            name=call["name"],
            status="success" if result.status == "ok" else "error",
        )


def _finished(result: ToolResult, run_id: str | None) -> events.ToolCallFinished:
    return events.ToolCallFinished(
        run_id=run_id,
        tool_call_id=result.tool_call_id,
        tool=result.tool,
        status="rejected" if result.status == "denied" else result.status,
        duration_ms=result.duration_ms,
        result_preview=result.preview,
        artifact_ref=result.artifact_ref,
    )
