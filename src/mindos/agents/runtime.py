"""The LangChain agent runtime behind the `AgentRuntime` port (ADR 0003).

One run is one worker task. It streams the orchestrator graph, turns model output into
application events, and shares an ordered queue with the tool bridge (which adds tool and
subagent events). The consumer side is an async generator, so cancelling the consumer cancels
the worker and the model call with it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any
from zoneinfo import ZoneInfo

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from mindos.agents.bridge import EngineMiddleware, stub_tools
from mindos.agents.hub import DONE, RunHub, RunState
from mindos.agents.limits import RunLimits
from mindos.agents.messages import split_content, usage_of
from mindos.agents.prompts import orchestrator_prompt
from mindos.agents.subagents import ChatModels, SubagentRunner
from mindos.agents.tuning import ModelTuner
from mindos.agents.usage import UsageTracker
from mindos.domain import (
    AppEvent,
    ApprovalRecord,
    Clock,
    EventEmit,
    IdFactory,
    ModelRole,
    ReasoningEffort,
    RunSpec,
    ToolResult,
    Tracing,
    bind,
    events,
)
from mindos.domain.commands import Decide
from mindos.tools import ToolExecutionEngine

log = logging.getLogger(__name__)

ORCHESTRATOR = "orchestrator"


class LangChainRuntime:
    def __init__(
        self,
        *,
        models: ChatModels,
        engine: ToolExecutionEngine,
        hub: RunHub,
        tracker: UsageTracker,
        runner: SubagentRunner,
        checkpointer: BaseCheckpointSaver[Any],
        limits: RunLimits,
        clock: Clock,
        tz: ZoneInfo,
        new_id: IdFactory,
        tuner: ModelTuner,
        tracing: Tracing | None = None,
    ) -> None:
        self._models = models
        self._engine = engine
        self._hub = hub
        self._tracker = tracker
        self._runner = runner
        self._checkpointer = checkpointer
        self._limits = limits
        self._clock = clock
        self._tz = tz
        self._new_id = new_id
        self._tuner = tuner
        self._tracing = tracing
        self._graphs: dict[tuple[str, ReasoningEffort], Any] = {}

    # ---- port ------------------------------------------------------------------------------
    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        ids = {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "thread_id": run.thread_id,
        }
        return self._stream(run.run_id, ids, lambda state: self._drive(run, state))

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        return self._resume(thread_id, decision)

    async def run_approved(self, record: ApprovalRecord, emit: EventEmit) -> ToolResult:
        """Run a deferred approval (a subagent's) through the engine. No graph is involved."""
        return await self._engine.run_approved(record, emit)

    async def _resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        """Deliver a decision to the graph paused on this approval.

        The checkpoint is the source of truth for what is paused, so the run's IDs and the
        interrupt to answer come from it, not from the caller. With several approvals pending
        (parallel tool calls) LangGraph needs the interrupt ID. If nothing is paused on this
        approval it was already delivered; that is a no-op, never an error.
        """
        found = await self._find_interrupt(thread_id, decision.approval_id)
        if found is None:
            log.info("no paused run is waiting on %s; nothing to resume", decision.approval_id)
            return
        interrupt_id, payload, effort = found
        run_id = str(payload.get("run_id") or thread_id)
        ids = {
            "run_id": run_id,
            "conversation_id": payload.get("conversation_id"),
            "thread_id": thread_id,
        }
        answer = {"approval_id": decision.approval_id, "comment": decision.comment}
        async for event in self._stream(
            run_id,
            ids,
            lambda state: self._drive_resume(thread_id, {interrupt_id: answer}, effort, state),
        ):
            yield event

    async def _find_interrupt(
        self, thread_id: str, approval_id: str
    ) -> tuple[str, dict[str, Any], ReasoningEffort] | None:
        """The interrupt waiting on this approval, and the effort its run started with."""
        snapshot = await self.graph().aget_state({"configurable": {"thread_id": thread_id}})
        for task in snapshot.tasks:
            for pending in task.interrupts:
                value = pending.value
                if isinstance(value, dict) and value.get("approval_id") == approval_id:
                    return str(pending.id), value, self._saved_effort(snapshot.metadata)
        return None

    def _saved_effort(self, metadata: dict[str, Any] | None) -> ReasoningEffort:
        """Effort is recorded in each checkpoint's metadata; a missing or unknown one is default."""
        try:
            return ReasoningEffort(str((metadata or {}).get("effort")))
        except ValueError:
            return self._default_effort

    async def cancel(self, run_id: str) -> None:
        state = self._hub.get(run_id)
        if state is not None and state.task is not None and not state.task.done():
            state.task.cancel()

    @property
    def tuner(self) -> ModelTuner:
        return self._tuner

    @property
    def _default_effort(self) -> ReasoningEffort:
        return self._tuner.effort(ModelRole.ORCHESTRATOR) or ReasoningEffort.HIGH

    # ---- graph -----------------------------------------------------------------------------
    def graph(self, effort: ReasoningEffort | None = None) -> Any:
        """The orchestrator agent for the current model and an effort. Built once per pair."""
        level = effort or self._default_effort
        key = (self._models.model_id(ModelRole.ORCHESTRATOR), level)
        if key not in self._graphs:
            subagents = "\n".join(f"- {d.name}: {d.description}" for d in self._runner.definitions)
            self._graphs[key] = create_deep_agent(
                self._models.chat(ModelRole.ORCHESTRATOR, level),
                tools=stub_tools(self._engine, ORCHESTRATOR),
                middleware=[
                    EngineMiddleware(
                        engine=self._engine,
                        hub=self._hub,
                        agent=ORCHESTRATOR,
                        system_prompt=lambda: orchestrator_prompt(
                            now=self._clock.now(), tz=self._tz, subagents=subagents
                        ),
                        can_pause=True,  # the orchestrator graph is checkpointed
                    )
                ],
                checkpointer=self._checkpointer,
                name=ORCHESTRATOR,
            )
        return self._graphs[key]

    # ---- run -------------------------------------------------------------------------------
    async def _stream(
        self,
        run_id: str,
        ids: dict[str, Any],
        drive: Callable[[RunState], Awaitable[None]],
    ) -> AsyncIterator[AppEvent]:
        """Run `drive` as a worker task and yield its ordered events until it finishes."""
        state = self._hub.open(run_id, self._limits)
        state.task = asyncio.create_task(self._work(ids, state, drive), name=f"work-{run_id}")
        try:
            while True:
                item = await state.queue.get()
                if item is DONE:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item  # type: ignore[misc]
        finally:
            state.task.cancel()
            await asyncio.gather(state.task, return_exceptions=True)
            self._hub.close(run_id)
            self._engine.end_run(run_id)  # a resume re-reads taint from the approval record

    async def _work(
        self,
        ids: dict[str, Any],
        state: RunState,
        drive: Callable[[RunState], Awaitable[None]],
    ) -> None:
        try:
            # The tool bridge finds the run through these IDs, so the runtime binds them itself.
            with bind(**ids), self._trace(ids):
                async with asyncio.timeout(self._limits.deadline_s):
                    await drive(state)
        except TimeoutError:
            self._stop(state, f"the {self._limits.deadline_s:g}s run deadline passed")
        except GraphRecursionError:
            self._stop(state, f"the step limit of {self._limits.max_steps} was reached")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.queue.put_nowait(exc)
        finally:
            state.queue.put_nowait(DONE)

    def _trace(self, ids: dict[str, Any]) -> AbstractContextManager[None]:
        """One trace per run, tagged with its thread. A resume continues the same thread."""
        if self._tracing is None:
            return nullcontext()
        return self._tracing.run(
            run_id=str(ids["run_id"]),
            thread_id=str(ids["thread_id"]),
            conversation_id=ids.get("conversation_id"),
        )

    @staticmethod
    def _stop(state: RunState, reason: str) -> None:
        """A cap was hit: end honestly with a partial result instead of failing."""
        text = f"Stopped because {reason}. The work is not finished; ask me to continue."
        state.emit(events.Notification(run_id=state.run_id, level="warning", text=text))
        state.emit(events.RunFinished(run_id=state.run_id, summary=f"partial: {reason}"))

    async def _drive(self, run: RunSpec, state: RunState) -> None:
        effort = run.effort or self._default_effort
        graph = self.graph(effort)
        config = self._config(run.thread_id, effort)
        await self._consume(
            graph, {"messages": [HumanMessage(run.user_text)]}, config, state, effort
        )

    async def _drive_resume(
        self, thread_id: str, resume: dict[str, Any], effort: ReasoningEffort, state: RunState
    ) -> None:
        await self._consume(
            self.graph(effort),
            Command(resume=resume),
            self._config(thread_id, effort),
            state,
            effort,
        )

    def _config(self, thread_id: str, effort: ReasoningEffort) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "run_name": "mindos.orchestrator",
            # Kept in the checkpoint (a resume reads the effort) and attached to the trace.
            "metadata": {"effort": effort.value, "thread_id": thread_id},
            "recursion_limit": self._limits.recursion(self._limits.max_steps),
        }

    async def _consume(
        self,
        graph: Any,
        graph_input: Any,
        config: dict[str, Any],
        state: RunState,
        effort: ReasoningEffort,
    ) -> None:
        message_id = self._new_id("msg")
        stream = graph.astream(graph_input, config, stream_mode=["messages", "updates"])
        async for mode, data in stream:
            if mode == "messages":
                self._on_chunk(state, message_id, *data)
            elif mode == "updates":
                for node, update in data.items():
                    if node != "model":
                        continue
                    for message in update.get("messages", ()):
                        if isinstance(message, AIMessage):
                            await self._on_message(state, message_id, message, effort)
                            message_id = self._new_id("msg")

    @staticmethod
    def _on_chunk(state: RunState, message_id: str, chunk: Any, meta: dict[str, Any]) -> None:
        # Only the orchestrator's own model node. Subagent output must never stream up.
        if meta.get("langgraph_node") != "model" or "|" in meta.get("langgraph_checkpoint_ns", ""):
            return
        text, thinking = split_content(chunk.content)
        if thinking:
            state.emit(
                events.ThinkingDelta(run_id=state.run_id, message_id=message_id, text=thinking)
            )
        if text:
            state.emit(
                events.AssistantTextDelta(run_id=state.run_id, message_id=message_id, text=text)
            )

    async def _on_message(
        self, state: RunState, message_id: str, message: AIMessage, effort: ReasoningEffort
    ) -> None:
        text, thinking = split_content(message.content)
        usage = usage_of(message)
        if thinking:
            state.emit(
                events.ThinkingComplete(
                    run_id=state.run_id,
                    message_id=message_id,
                    tokens=usage.reasoning_tokens or None,
                )
            )
        if text:
            state.emit(
                events.AssistantMessageComplete(
                    run_id=state.run_id, message_id=message_id, text=text
                )
            )
        await self._tracker.record(
            state,
            role=ModelRole.ORCHESTRATOR,
            model=self._models.model_id(ModelRole.ORCHESTRATOR),
            usage=usage,
            effort=effort,
        )
