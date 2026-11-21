from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from opsagent.agents import UnavailableRuntime
from opsagent.application import Application, EventBus
from opsagent.domain import AppEvent, RunSpec, events
from opsagent.domain.commands import Decide, SubmitMessage
from opsagent.infra import new_id


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)


class ScriptedRuntime:
    """Emits a fixed script, optionally including its own terminal event."""

    def __init__(self, *, terminal: bool = False, raises: Exception | None = None) -> None:
        self.terminal, self.raises, self.cancelled = terminal, raises, []

    async def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        yield events.AssistantTextDelta(run_id=run.run_id, message_id="m1", text="hel")
        if self.raises:
            raise self.raises
        yield events.AssistantTextDelta(run_id=run.run_id, message_id="m1", text="lo")
        if self.terminal:
            yield events.RunFinished(run_id=run.run_id, summary="done")

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        raise NotImplementedError

    async def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)


class BlockingRuntime(ScriptedRuntime):
    async def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        yield events.AssistantTextDelta(run_id=run.run_id, message_id="m1", text="x")
        await asyncio.sleep(60)


async def _run(runtime: ScriptedRuntime) -> tuple[Application, list[AppEvent]]:
    bus = EventBus(FixedClock())
    app = Application(runtime=runtime, bus=bus, new_id=new_id)
    with bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="hi"))
        seen: list[AppEvent] = []
        async for ev in sub:
            seen.append(ev)
            if isinstance(ev, events.RunFinished | events.RunFailed | events.RunCancelled):
                break
    return app, seen


async def test_sequence_numbers_and_stamps() -> None:
    _, seen = await _run(ScriptedRuntime())
    assert [e.type for e in seen] == [
        "run.started",
        "assistant.text.delta",
        "assistant.text.delta",
        "run.finished",
    ]
    assert [e.seq for e in seen] == [1, 2, 3, 4]
    assert all(e.ts == datetime(2026, 1, 1, tzinfo=UTC) for e in seen)
    assert all("run_id" in e.correlation for e in seen)


async def test_runtime_terminal_event_is_not_duplicated() -> None:
    _, seen = await _run(ScriptedRuntime(terminal=True))
    assert [e.type for e in seen].count("run.finished") == 1
    assert seen[-1].summary == "done"  # type: ignore[union-attr]


async def test_runtime_error_becomes_run_failed_with_redacted_message() -> None:
    _, seen = await _run(ScriptedRuntime(raises=RuntimeError("bad key AIza" + "Q" * 35)))
    failed = seen[-1]
    assert isinstance(failed, events.RunFailed)
    assert failed.code == "RuntimeError"
    assert "AIza" not in failed.message


async def test_unavailable_runtime_fails_cleanly() -> None:
    _, seen = await _run(UnavailableRuntime())  # type: ignore[arg-type]
    assert isinstance(seen[-1], events.RunFailed)
    assert "not connected" in seen[-1].message


async def test_cancel_publishes_run_cancelled_once() -> None:
    runtime = BlockingRuntime()
    bus = EventBus(FixedClock())
    app = Application(runtime=runtime, bus=bus, new_id=new_id)
    run_id = app.submit_message(SubmitMessage(text="hi"))
    with bus.subscribe(run_id) as sub:
        await asyncio.sleep(0.05)
        assert await app.cancel(run_id) is True
        types = [e.type async for e in sub]
    assert types[-1] == "run.cancelled"
    assert types.count("run.cancelled") == 1
    assert runtime.cancelled == [run_id]
    assert await app.cancel(run_id) is False


async def test_run_scoped_subscription_ends_after_terminal_event() -> None:
    bus = EventBus(FixedClock())
    with bus.subscribe("r1") as sub:
        bus.publish(events.Notification(run_id="r2", text="other run"))
        bus.publish(
            events.RunStarted(run_id="r1", conversation_id="c", thread_id="t", user_text="u")
        )
        bus.publish(events.RunFinished(run_id="r1"))
        got = [e.type async for e in sub]
    assert got == ["run.started", "run.finished"]


async def test_sequence_state_is_dropped_after_terminal() -> None:
    bus = EventBus(FixedClock())
    bus.publish(events.RunStarted(run_id="r1", conversation_id="c", thread_id="t", user_text="u"))
    bus.publish(events.RunFinished(run_id="r1"))
    assert bus._seq == {}


async def test_aclose_cancels_active_runs() -> None:
    bus = EventBus(FixedClock())
    app = Application(runtime=BlockingRuntime(), bus=bus, new_id=new_id)
    with bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="hi"))
        await asyncio.sleep(0.05)
        await app.aclose()
        assert sub.pending() >= 3  # started, delta, cancelled
