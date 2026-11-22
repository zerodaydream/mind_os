"""The LangChain agent runtime behind the `AgentRuntime` port (ADR 0003).

One run is one worker task. It streams the orchestrator graph, turns model output into
application events, and shares an ordered queue with the tool bridge (which adds tool and
subagent events). The consumer side is an async generator, so cancelling the consumer cancels
the worker and the model call with it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from opsagent.agents.bridge import EngineMiddleware, stub_tools
from opsagent.agents.hub import DONE, RunHub, RunState
from opsagent.agents.limits import RunLimits
from opsagent.agents.messages import split_content, usage_of
from opsagent.agents.prompts import orchestrator_prompt
from opsagent.agents.subagents import ChatModels, SubagentRunner
from opsagent.agents.usage import UsageTracker
from opsagent.domain import (
    AppEvent,
    Clock,
    IdFactory,
    ModelRole,
    ReasoningEffort,
    RunSpec,
    bind,
    events,
)
from opsagent.domain.commands import Decide
from opsagent.tools import ToolExecutionEngine

log = logging.getLogger(__name__)

ORCHESTRATOR = "orchestrator"
_INTERRUPTED = "Interrupted before this tool ran. The result is unknown; do not assume it worked."


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
        default_effort: ReasoningEffort,
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
        self._default_effort = default_effort
        self._graphs: dict[ReasoningEffort, Any] = {}

    # ---- port ------------------------------------------------------------------------------
    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        return self._stream(run)

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        return self._stream_resume(thread_id, decision)

    async def _stream_resume(
        self, thread_id: str, decision: Decide
    ) -> AsyncIterator[AppEvent]:
        """Resume a graph that was interrupted waiting for an approval decision.

        Uses the thread_id as the hub run_id for the duration of the resume so the bridge
        can emit events. Events stream just like _stream.
        """
        run_id = thread_id
        state = self._hub.open(run_id, self._limits)
        outcome_map: dict[str, Any] = {
            "approve": "approved",
            "edit": "edited",
            "reject": "rejected",
        }
        outcome = outcome_map.get(decision.decision, "rejected")
        state.emit(
            events.ApprovalResolved(
                run_id=run_id,
                approval_id=decision.approval_id,
                outcome=outcome,
            )
        )
        resume_value: dict[str, Any] = {
            "action": decision.decision,
            "approval_id": decision.approval_id,
            "edited_args": decision.edited_args,
            "comment": decision.comment,
        }
        state.task = asyncio.create_task(
            self._work_resume(thread_id, resume_value, state),
            name=f"resume-{thread_id}",
        )
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

    async def _work_resume(
        self, thread_id: str, resume_value: dict[str, Any], state: RunState
    ) -> None:
        try:
            async with asyncio.timeout(self._limits.deadline_s):
                await self._drive_resume(thread_id, resume_value, state)
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

    async def _drive_resume(
        self, thread_id: str, resume_value: dict[str, Any], state: RunState
    ) -> None:
        effort = self._default_effort
        graph = self.graph(effort)
        config: dict[str, Any] = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": self._limits.recursion(self._limits.max_steps),
        }
        message_id = self._new_id("msg")
        stream = graph.astream(
            Command(resume=resume_value),
            config,
            stream_mode=["messages", "updates"],
        )
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

    async def cancel(self, run_id: str) -> None:
        state = self._hub.get(run_id)
        if state is not None and state.task is not None and not state.task.done():
            state.task.cancel()

    # ---- graph -----------------------------------------------------------------------------
    def graph(self, effort: ReasoningEffort | None = None) -> Any:
        """The orchestrator agent for an effort level. Built once per level."""
        level = effort or self._default_effort
        if level not in self._graphs:
            subagents = "\n".join(f"- {d.name}: {d.description}" for d in self._runner.definitions)
            self._graphs[level] = create_agent(
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
                    )
                ],
                checkpointer=self._checkpointer,
                name=ORCHESTRATOR,
            )
        return self._graphs[level]

    # ---- run -------------------------------------------------------------------------------
    async def _stream(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        state = self._hub.open(run.run_id, self._limits)
        state.task = asyncio.create_task(self._work(run, state), name=f"work-{run.run_id}")
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
            self._hub.close(run.run_id)

    async def _work(self, run: RunSpec, state: RunState) -> None:
        try:
            # The tool bridge finds the run through these IDs, so the runtime binds them itself.
            with bind(
                run_id=run.run_id, conversation_id=run.conversation_id, thread_id=run.thread_id
            ):
                async with asyncio.timeout(self._limits.deadline_s):
                    await self._drive(run, state)
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

    @staticmethod
    def _stop(state: RunState, reason: str) -> None:
        """A cap was hit: end honestly with a partial result instead of failing."""
        text = f"Stopped because {reason}. The work is not finished; ask me to continue."
        state.emit(events.Notification(run_id=state.run_id, level="warning", text=text))
        state.emit(events.RunFinished(run_id=state.run_id, summary=f"partial: {reason}"))

    async def _drive(self, run: RunSpec, state: RunState) -> None:
        effort = run.effort or self._default_effort
        graph = self.graph(effort)
        config = {
            "configurable": {"thread_id": run.thread_id},
            "recursion_limit": self._limits.recursion(self._limits.max_steps),
        }
        await self._repair_thread(graph, config)
        message_id = self._new_id("msg")
        stream = graph.astream(
            {"messages": [HumanMessage(run.user_text)]},
            config,
            stream_mode=["messages", "updates"],
        )
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

    @staticmethod
    async def _repair_thread(graph: Any, config: dict[str, Any]) -> None:
        """Answer tool calls left dangling by an earlier stop or cancel.

        Providers reject a history that has a tool call without a response. After a step limit,
        deadline, or Esc the last model message may still hold unanswered calls.
        """
        snapshot = await graph.aget_state(config)
        messages = list(snapshot.values.get("messages", ())) if snapshot.values else []
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, AIMessage) and message.tool_calls:
                answered = {
                    m.tool_call_id for m in messages[index + 1 :] if isinstance(m, ToolMessage)
                }
                dangling = [c for c in message.tool_calls if c["id"] not in answered]
                if dangling:
                    fixes = [
                        ToolMessage(
                            content=_INTERRUPTED,
                            tool_call_id=c["id"] or "",
                            name=c["name"],
                            status="error",
                        )
                        for c in dangling
                    ]
                    await graph.aupdate_state(config, {"messages": fixes}, as_node="tools")
                return
