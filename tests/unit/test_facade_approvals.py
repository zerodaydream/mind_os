from __future__ import annotations

import asyncio

from mindos.agents.fake import FakeRuntime
from mindos.application import Application, EventBus
from mindos.domain import ReasoningEffort, RunSpec, events
from mindos.domain.commands import Decide, SetMode, SubmitMessage
from mindos.domain.risk import ApprovalMode
from mindos.infra import SystemClock, new_id


def make() -> Application:
    return Application(runtime=FakeRuntime(speed=100), bus=EventBus(SystemClock()), new_id=new_id)


async def collect_until(sub, stop_types: tuple[str, ...]) -> list[str]:  # type: ignore[no-untyped-def]
    seen: list[str] = []
    async for ev in sub:
        seen.append(ev.type)
        if ev.type in stop_types:
            break
    return seen


async def test_run_suspends_on_approval_without_a_terminal_event() -> None:
    app = make()
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="approval please"))
        seen = await asyncio.wait_for(collect_until(sub, ("approval.requested",)), 5)
        await asyncio.sleep(0.1)
        assert sub.pending() == 0  # nothing after the request: no RunFinished
    assert seen[-1] == "approval.requested"


async def test_decide_resumes_and_finishes() -> None:
    app = make()
    with app.bus.subscribe() as sub:
        run_id = app.submit_message(SubmitMessage(text="approval"))
        await asyncio.wait_for(collect_until(sub, ("approval.requested",)), 5)
        assert app.decide(Decide(approval_id="apr_demo1", decision="approve"))
        seen = await asyncio.wait_for(collect_until(sub, ("run.finished",)), 5)
    assert seen[0] == "approval.resolved"
    assert "tool.finished" in seen
    assert run_id


async def test_reject_branch_and_unknown_approval() -> None:
    app = make()
    assert app.decide(Decide(approval_id="nope", decision="approve")) is False
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="approval"))
        await asyncio.wait_for(collect_until(sub, ("approval.requested",)), 5)
        app.decide(Decide(approval_id="apr_demo1", decision="reject"))
        texts = []
        async for ev in sub:
            if isinstance(ev, events.AssistantMessageComplete):
                texts.append(ev.text)
            if ev.type == "run.finished":
                break
    assert texts == ["Understood. I did not send anything."]


async def test_cancel_a_suspended_run() -> None:
    app = make()
    with app.bus.subscribe() as sub:
        run_id = app.submit_message(SubmitMessage(text="approval"))
        await asyncio.wait_for(collect_until(sub, ("approval.requested",)), 5)
        assert await app.cancel(run_id) is True
        seen = await asyncio.wait_for(collect_until(sub, ("run.cancelled",)), 5)
    assert seen == ["run.cancelled"]
    assert app.decide(Decide(approval_id="apr_demo1", decision="approve")) is False


async def test_set_mode_publishes_event() -> None:
    app = make()
    with app.bus.subscribe() as sub:
        app.set_mode(SetMode(mode=ApprovalMode.PLAN))
        ev = await asyncio.wait_for(sub.__anext__(), 2)
    assert isinstance(ev, events.ModeChanged) and ev.mode == ApprovalMode.PLAN


async def test_follow_up_messages_reuse_the_conversation_thread() -> None:
    seen: list[RunSpec] = []

    class Recording:
        def start(self, run: RunSpec):  # type: ignore[no-untyped-def]
            seen.append(run)
            return _empty()

        def resume(self, thread_id, decision):  # type: ignore[no-untyped-def]
            raise AssertionError

        async def cancel(self, run_id: str) -> None:
            return None

    async def _empty():  # type: ignore[no-untyped-def]
        return
        yield

    app = Application(runtime=Recording(), bus=EventBus(SystemClock()), new_id=new_id)
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="one", effort=ReasoningEffort.LOW))
        await asyncio.wait_for(collect_until(sub, ("run.finished",)), 5)
        first = seen[0]
        app.submit_message(SubmitMessage(text="two", conversation_id=first.conversation_id))
        app.submit_message(SubmitMessage(text="other"))
        await asyncio.wait_for(collect_until(sub, ("run.finished",)), 5)
        await asyncio.wait_for(collect_until(sub, ("run.finished",)), 5)
    assert first.effort is ReasoningEffort.LOW
    assert seen[1].thread_id == first.thread_id and seen[1].conversation_id == first.conversation_id
    assert seen[2].thread_id != first.thread_id
