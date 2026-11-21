"""Application facade: the only API the UI calls (ARCHITECTURE section 13).

A run is a task that pumps runtime events onto the bus and ends with exactly one terminal event.
A run that asks for approval is *suspended*: its task ends without a terminal event, and
`decide` later starts a new task that resumes it. Rewind and slash commands arrive with the
phases that own them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal

from opsagent.application.event_bus import EventBus
from opsagent.domain import (
    TERMINAL_EVENTS,
    AgentRuntime,
    AppEvent,
    IdFactory,
    RunSpec,
    bind,
    events,
    redact_text,
)
from opsagent.domain.commands import Decide, SetMode, SubmitMessage

log = logging.getLogger(__name__)

_Outcome = Literal["terminal", "suspended", "done"]


@dataclass(frozen=True)
class _RunContext:
    run_id: str
    conversation_id: str
    thread_id: str


class Application:
    def __init__(self, *, runtime: AgentRuntime, bus: EventBus, new_id: IdFactory) -> None:
        self._runtime = runtime
        self._bus = bus
        self._new_id = new_id
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._awaiting: dict[str, _RunContext] = {}  # approval_id -> suspended run
        self._threads: dict[str, str] = {}  # conversation_id -> thread_id (history lives there)

    @property
    def bus(self) -> EventBus:
        return self._bus

    # ---- commands --------------------------------------------------------------------------
    def submit_message(self, command: SubmitMessage) -> str:
        """Start a run and return its ID. Events arrive on the bus; this call does not wait."""
        conversation_id = command.conversation_id or self._new_id("conv")
        thread_id = self._threads.setdefault(conversation_id, self._new_id("thr"))
        ctx = _RunContext(
            run_id=self._new_id("run"), conversation_id=conversation_id, thread_id=thread_id
        )
        spec = RunSpec(**vars(ctx), user_text=command.text, effort=command.effort)

        def started() -> AppEvent:  # built inside the run's correlation context
            return events.RunStarted(
                run_id=ctx.run_id,
                conversation_id=ctx.conversation_id,
                thread_id=ctx.thread_id,
                user_text=command.text,
            )

        self._spawn(ctx, lambda: self._runtime.start(spec), announce=started)
        return ctx.run_id

    def decide(self, command: Decide) -> bool:
        """Resume a run that is waiting on this approval. False if the approval is unknown."""
        ctx = self._awaiting.pop(command.approval_id, None)
        if ctx is None:
            return False
        self._spawn(ctx, lambda: self._runtime.resume(ctx.thread_id, command))
        return True

    def set_mode(self, command: SetMode) -> None:
        """Announce a session mode change. Policy enforcement joins this in Phase 5."""
        self._bus.publish(events.ModeChanged(mode=command.mode))

    async def cancel(self, run_id: str) -> bool:
        """Cancel an active or suspended run. False if the run is unknown or already finished."""
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait({task})
            return True
        suspended = [a for a, c in self._awaiting.items() if c.run_id == run_id]
        if not suspended:
            return False
        ctx = self._awaiting[suspended[0]]
        for approval_id in suspended:
            del self._awaiting[approval_id]
        with bind(run_id=ctx.run_id, conversation_id=ctx.conversation_id, thread_id=ctx.thread_id):
            await self._cancel_runtime(ctx.run_id)
            self._bus.publish(events.RunCancelled(run_id=ctx.run_id, reason="cancelled"))
        return True

    async def aclose(self) -> None:
        """Cancel every active run and wait for each to publish its terminal event."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)

    # ---- run supervision -------------------------------------------------------------------
    def _spawn(
        self,
        ctx: _RunContext,
        stream: Callable[[], AsyncIterator[AppEvent]],
        announce: Callable[[], AppEvent] | None = None,
    ) -> None:
        self._tasks[ctx.run_id] = asyncio.create_task(
            self._supervise(ctx, stream, announce), name=ctx.run_id
        )

    async def _supervise(
        self,
        ctx: _RunContext,
        stream: Callable[[], AsyncIterator[AppEvent]],
        announce: Callable[[], AppEvent] | None,
    ) -> None:
        """Drive one stretch of a run. Publishes exactly one terminal event unless suspended."""
        publish = self._bus.publish
        with bind(run_id=ctx.run_id, conversation_id=ctx.conversation_id, thread_id=ctx.thread_id):
            if announce is not None:
                publish(announce())
            try:
                if await self._pump(ctx, stream()) == "done":
                    publish(events.RunFinished(run_id=ctx.run_id))
            except asyncio.CancelledError:
                await self._cancel_runtime(ctx.run_id)
                publish(events.RunCancelled(run_id=ctx.run_id, reason="cancelled"))
            except Exception as exc:
                log.exception("run failed")
                publish(
                    events.RunFailed(
                        run_id=ctx.run_id, code=type(exc).__name__, message=redact_text(str(exc))
                    )
                )
            finally:
                self._tasks.pop(ctx.run_id, None)

    async def _pump(self, ctx: _RunContext, stream: AsyncIterator[AppEvent]) -> _Outcome:
        async for event in stream:
            self._bus.publish(event)
            if isinstance(event, events.ApprovalRequested):
                self._awaiting[event.approval.approval_id] = ctx
            elif isinstance(event, TERMINAL_EVENTS):
                return "terminal"
        suspended = any(c.run_id == ctx.run_id for c in self._awaiting.values())
        return "suspended" if suspended else "done"

    async def _cancel_runtime(self, run_id: str) -> None:
        try:
            await asyncio.shield(self._runtime.cancel(run_id))
        except Exception:
            log.exception("runtime cancel failed")
