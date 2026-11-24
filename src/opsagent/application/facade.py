"""Application facade: the only API the UI calls (ARCHITECTURE section 13).

A run is a task that pumps runtime events onto the bus and ends with exactly one terminal event.
A run that asks for approval is *suspended*: its task ends without a terminal event, and
`decide` later starts a new stretch that resumes it. Stretches of one run never overlap (a run
can have several approvals pending), and a run is only suspended while some approval of it has
not yet been delivered to the paused graph. After a restart `recover` rebuilds all of this from
the database. Rewind and slash commands arrive with the phases that own them.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from opsagent.application.approval_broker import view_of
from opsagent.application.event_bus import EventBus
from opsagent.domain import (
    TERMINAL_EVENTS,
    AgentRuntime,
    AppEvent,
    ApprovalBrokerPort,
    ApprovalRecord,
    ApprovedCallRunner,
    IdFactory,
    RunSpec,
    SessionMode,
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
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        bus: EventBus,
        new_id: IdFactory,
        broker: ApprovalBrokerPort | None = None,
        executor: ApprovedCallRunner | None = None,
        mode: SessionMode | None = None,
    ) -> None:
        self._runtime = runtime
        self._bus = bus
        self._new_id = new_id
        self._broker = broker
        self._executor = executor
        self._mode = mode or SessionMode()
        self._tasks: dict[str, list[asyncio.Task[None]]] = {}  # run_id -> live stretches
        self._locks: dict[str, asyncio.Lock] = {}  # run_id -> one stretch at a time
        self._awaiting: dict[str, _RunContext] = {}  # approval_id -> run awaiting the user
        self._undelivered: dict[str, set[str]] = {}  # run_id -> approvals the graph has not got
        self._deferred: set[str] = set()  # approvals no graph waits on (a subagent asked)
        self._deferred_tasks: set[asyncio.Task[None]] = set()
        self._threads: dict[str, str] = {}  # conversation_id -> thread_id (history lives there)
        self._closing = False  # shutting down: stop work, but keep approvals for the next start

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
        """Resume a run that is waiting on this approval. False if the approval is unknown.

        Claiming the approval here makes a double click harmless: the second call gets False.
        """
        ctx = self._awaiting.pop(command.approval_id, None)
        if ctx is None:
            return False
        if command.approval_id in self._deferred:
            self._spawn_deferred(ctx, self._settle_deferred(ctx, command))
            return True
        self._spawn(ctx, lambda: self._decide(ctx, command))
        return True

    def set_mode(self, command: SetMode) -> None:
        """Switch the session mode. The policy engine reads it; it can only tighten policy."""
        self._mode.set(command.mode)
        self._bus.publish(events.ModeChanged(mode=command.mode))

    async def cancel(self, run_id: str) -> bool:
        """Cancel an active or suspended run. False if the run is unknown or already finished."""
        live = [t for t in self._tasks.get(run_id, ()) if not t.done()]
        suspended = bool(self._undelivered.get(run_id))
        if not live and not suspended:
            return False
        for task in live:
            task.cancel()
        if live:
            await asyncio.wait(live)  # each stretch publishes RunCancelled itself
            return True
        ctx = next((c for c in self._awaiting.values() if c.run_id == run_id), None)
        ids = {"thread_id": ctx.thread_id} if ctx else {}
        with bind(run_id=run_id, **ids):
            await self._cancel_runtime(run_id)
            await self._abandon(run_id)
            self._bus.publish(events.RunCancelled(run_id=run_id, reason="cancelled"))
        return True

    async def aclose(self) -> None:
        """Stop every active run and wait for each to publish its terminal event.

        Closing is not cancelling: pending approvals stay PENDING for the next start
        (ARCHITECTURE 11.4, "terminal closed").
        """
        self._closing = True
        tasks = [t for group in self._tasks.values() for t in group] + list(self._deferred_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)

    async def recover(self) -> int:
        """Reconciliation step 2 (ARCHITECTURE 11.4): rebuild approval state after a restart.

        Pending approvals become dialogs again; decisions made before the restart but never
        delivered to the paused graph are delivered now; an approval that expired while the app
        was closed is not resumed unattended. Returns how many approvals were picked up.
        """
        if self._broker is None:
            return 0
        try:
            records = await self._broker.undelivered()
        except Exception:
            log.exception("could not read undelivered approvals")
            return 0
        abandoned = 0
        for record in records:
            ctx = _context_of(record)
            if ctx is None:
                continue
            if record.deferred:
                await self._recover_deferred(ctx, record)
            elif record.state == "PENDING":
                self._rehydrate(ctx, record)
            elif record.state == "EXPIRED":
                await self._broker.mark_delivered(record.id)
                abandoned += 1
            else:
                self._redeliver(ctx, record)
        if abandoned:
            self._bus.publish(
                events.Notification(
                    run_id=None,
                    level="warning",
                    text=f"{abandoned} approval(s) expired while the app was closed; "
                    "those runs were not resumed.",
                )
            )
        return len(records) - abandoned

    # ---- approvals -------------------------------------------------------------------------
    # A deferred approval belongs to a subagent, which cannot pause, so its run has already moved
    # on. The user's decision is applied here and, if approved, the application runs the call
    # through the engine itself. The model is not resumed: the user is told what happened.
    def _spawn_deferred(self, ctx: _RunContext, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(work, name=f"deferred-{ctx.run_id}")
        self._deferred_tasks.add(task)
        task.add_done_callback(self._deferred_tasks.discard)

    async def _settle_deferred(self, ctx: _RunContext, command: Decide) -> None:
        assert self._broker is not None
        approval_id = command.approval_id
        with bind(run_id=ctx.run_id, conversation_id=ctx.conversation_id, thread_id=ctx.thread_id):
            resolution = await self._broker.resolve(
                approval_id,
                decision=command.decision,
                edited_args=command.edited_args,
                comment=command.comment,
            )
            if resolution.outcome == "refused":
                self._awaiting[approval_id] = ctx
                self._notify(ctx, "warning", f"Edit not accepted: {resolution.detail}.")
                return
            if resolution.outcome == "unavailable":
                self._notify(ctx, "dim", f"That approval is {resolution.detail}.")
                return
            if resolution.outcome == "expired":
                self._bus.publish(
                    events.ApprovalExpired(run_id=ctx.run_id, approval_id=approval_id)
                )
                await self._broker.mark_delivered(approval_id)
                return
            self._bus.publish(
                events.ApprovalResolved(
                    run_id=ctx.run_id, approval_id=approval_id, outcome=resolution.outcome
                )
            )
            record = await self._broker.get(approval_id)
            if resolution.outcome == "rejected" or record is None:
                await self._broker.mark_delivered(approval_id)
                return
            await self._run_deferred(ctx, record)

    async def _run_deferred(self, ctx: _RunContext, record: ApprovalRecord) -> None:
        """Execute an approved deferred call and tell the user the truth about the outcome."""
        assert self._broker is not None
        if self._executor is None:
            self._notify(ctx, "warning", f"{record.summary}: approved, but nothing can run it.")
            return
        try:
            result = await self._executor.run_approved(record, self._emit)
        except asyncio.CancelledError:
            raise  # shutting down: the claim (if any) stays for reconciliation to report
        except Exception:
            log.exception("deferred call %s failed", record.id)
            self._notify(ctx, "warning", f"{record.summary}: failed unexpectedly.")
        else:
            if result.status == "ok":
                self._notify(ctx, "info", f"{record.summary}: done. {result.preview}")
            else:
                self._notify(
                    ctx,
                    "warning",
                    f"{record.summary}: not done ({result.error_code}). Nothing else ran.",
                )
        await self._broker.mark_delivered(record.id)

    async def _recover_deferred(self, ctx: _RunContext, record: ApprovalRecord) -> None:
        """After a restart: ask again if pending; finish an approved call the app never ran."""
        assert self._broker is not None
        if record.state == "PENDING":
            self._awaiting[record.id] = ctx
            self._deferred.add(record.id)
            self._bus.publish(events.ApprovalRequested(run_id=ctx.run_id, approval=view_of(record)))
        elif record.state in ("APPROVED", "APPROVED_EDITED"):
            self._spawn_deferred(ctx, self._run_deferred(ctx, record))
        else:  # decided and settled (or expired) while the app was closed: nothing is waiting
            await self._broker.mark_delivered(record.id)

    def _emit(self, event: AppEvent) -> None:
        self._bus.publish(event)

    def _notify(
        self, ctx: _RunContext, level: Literal["info", "warning", "dim"], text: str
    ) -> None:
        self._bus.publish(events.Notification(run_id=ctx.run_id, level=level, text=text))

    async def _decide(self, ctx: _RunContext, command: Decide) -> AsyncIterator[AppEvent]:
        """Record the user's decision, say what happened, then deliver it to the paused graph."""
        if self._broker is None:  # demo runtime: it owns the whole flow
            async for event in self._deliver(ctx, command):
                yield event
            return
        resolution = await self._broker.resolve(
            command.approval_id,
            decision=command.decision,
            edited_args=command.edited_args,
            comment=command.comment,
        )
        approval_id = command.approval_id
        if resolution.outcome == "refused":
            self._awaiting[approval_id] = ctx  # still pending: edit again, approve, or reject
            yield events.Notification(
                run_id=ctx.run_id, level="warning", text=f"Edit not accepted: {resolution.detail}."
            )
            return
        if resolution.outcome == "unavailable":
            self._undelivered.get(ctx.run_id, set()).discard(approval_id)  # whoever resolved it
            yield events.Notification(
                run_id=ctx.run_id, level="dim", text=f"That approval is {resolution.detail}."
            )
            return
        if resolution.outcome == "expired":
            yield events.ApprovalExpired(run_id=ctx.run_id, approval_id=approval_id)
        else:
            yield events.ApprovalResolved(
                run_id=ctx.run_id, approval_id=approval_id, outcome=resolution.outcome
            )
        async for event in self._deliver(ctx, command):
            yield event

    async def _deliver(self, ctx: _RunContext, command: Decide) -> AsyncIterator[AppEvent]:
        """Resume the paused graph, then record that it has the outcome."""
        async for event in self._runtime.resume(ctx.thread_id, command):
            yield event
        self._undelivered.get(ctx.run_id, set()).discard(command.approval_id)
        if self._broker is None:
            return
        await self._broker.mark_delivered(command.approval_id)
        record = await self._broker.get(command.approval_id)
        if record is not None and record.state in ("APPROVED", "APPROVED_EDITED"):
            # Nothing was paused on it (the process died between writing the approval and
            # checkpointing the pause), so the approved call never ran. Say so; do not pretend.
            await self._broker.invalidate(command.approval_id, reason="run_not_paused")
            yield events.Notification(
                run_id=ctx.run_id,
                level="warning",
                text="An approved action was not executed: its run was no longer waiting. "
                "Ask again if you still want it.",
            )

    def _rehydrate(self, ctx: _RunContext, record: ApprovalRecord) -> None:
        """A pending approval from before the restart: show it again."""
        self._awaiting[record.id] = ctx
        self._undelivered.setdefault(ctx.run_id, set()).add(record.id)
        self._bus.publish(events.ApprovalRequested(run_id=ctx.run_id, approval=view_of(record)))

    def _redeliver(self, ctx: _RunContext, record: ApprovalRecord) -> None:
        """A decision that never reached the paused graph. The engine reads the real state."""
        granted = record.state in ("APPROVED", "APPROVED_EDITED")
        command = Decide(approval_id=record.id, decision="approve" if granted else "reject")
        self._undelivered.setdefault(ctx.run_id, set()).add(record.id)
        self._spawn(ctx, lambda: self._deliver(ctx, command))

    async def _abandon(self, run_id: str) -> None:
        """A run was cancelled: cancel its approvals and stop tracking it."""
        self._undelivered.pop(run_id, None)
        for approval_id in [a for a, c in self._awaiting.items() if c.run_id == run_id]:
            del self._awaiting[approval_id]
        if self._broker is not None:
            try:
                await self._broker.cancel_for_run(run_id)
            except Exception:
                log.exception("could not cancel approvals of %s", run_id)

    # ---- run supervision -------------------------------------------------------------------
    def _spawn(
        self,
        ctx: _RunContext,
        stream: Callable[[], AsyncIterator[AppEvent]],
        announce: Callable[[], AppEvent] | None = None,
    ) -> None:
        task = asyncio.create_task(self._supervise(ctx, stream, announce), name=ctx.run_id)
        self._tasks.setdefault(ctx.run_id, []).append(task)

    async def _supervise(
        self,
        ctx: _RunContext,
        stream: Callable[[], AsyncIterator[AppEvent]],
        announce: Callable[[], AppEvent] | None,
    ) -> None:
        """Drive one stretch of a run. Publishes exactly one terminal event unless suspended."""
        publish = self._bus.publish
        lock = self._locks.setdefault(ctx.run_id, asyncio.Lock())
        outcome: _Outcome = "terminal"
        with bind(run_id=ctx.run_id, conversation_id=ctx.conversation_id, thread_id=ctx.thread_id):
            try:
                async with lock:  # one stretch of a run at a time
                    if announce is not None:
                        publish(announce())
                    outcome = await self._pump(ctx, stream())
                    if outcome == "done":
                        publish(events.RunFinished(run_id=ctx.run_id))
            except asyncio.CancelledError:
                await self._cancel_runtime(ctx.run_id)
                if not self._closing:  # the user cancelled the run; shutting down is different
                    await self._abandon(ctx.run_id)
                publish(events.RunCancelled(run_id=ctx.run_id, reason="cancelled"))
            except Exception as exc:
                log.exception("run failed")
                publish(
                    events.RunFailed(
                        run_id=ctx.run_id, code=type(exc).__name__, message=redact_text(str(exc))
                    )
                )
            finally:
                self._end_stretch(ctx.run_id, outcome)

    def _end_stretch(self, run_id: str, outcome: _Outcome) -> None:
        group = self._tasks.get(run_id, [])
        mine = asyncio.current_task()
        if mine is not None and mine in group:
            group.remove(mine)
        if not group:
            self._tasks.pop(run_id, None)
        if outcome != "suspended" and not self._undelivered.get(run_id):
            self._undelivered.pop(run_id, None)
            if run_id not in self._tasks:
                self._locks.pop(run_id, None)

    async def _pump(self, ctx: _RunContext, stream: AsyncIterator[AppEvent]) -> _Outcome:
        async for event in stream:
            self._bus.publish(event)
            if isinstance(event, events.ApprovalRequested):
                self._awaiting[event.approval.approval_id] = ctx
                if event.approval.deferred:  # nothing is paused on it: the run may finish
                    self._deferred.add(event.approval.approval_id)
                else:
                    self._undelivered.setdefault(ctx.run_id, set()).add(event.approval.approval_id)
            elif isinstance(event, TERMINAL_EVENTS):
                return "terminal"
        return "suspended" if self._undelivered.get(ctx.run_id) else "done"

    async def _cancel_runtime(self, run_id: str) -> None:
        try:
            await asyncio.shield(self._runtime.cancel(run_id))
        except Exception:
            log.exception("runtime cancel failed")


def _context_of(record: ApprovalRecord) -> _RunContext | None:
    if not (record.run_id and record.thread_id and record.conversation_id):
        log.warning("approval %s has no run context; it cannot be resumed", record.id)
        return None
    return _RunContext(record.run_id, record.conversation_id, record.thread_id)
