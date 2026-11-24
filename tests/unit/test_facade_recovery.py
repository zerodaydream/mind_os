"""The facade with the real runtime and broker: decisions, races, cancel, and crash recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from opsagent.application import Application, EventBus
from opsagent.domain import Principal, RiskClass, bind
from opsagent.domain.commands import Decide, SubmitMessage
from opsagent.infra import SystemClock, new_id
from tests.support.agent_kit import Kit
from tests.support.scripted_model import reply, tool_call
from tests.support.world import World

TODO_A = {"todos": [{"id": "1", "text": "first", "status": "pending"}]}
TODO_B = {"todos": [{"id": "2", "text": "second", "status": "pending"}]}


def app_for(world: World, kit: Kit) -> Application:
    return Application(
        runtime=kit.runtime, bus=EventBus(SystemClock()), new_id=new_id, broker=world.broker()
    )


async def until(sub: Any, *types: str, seen: list[Any] | None = None) -> Any:
    async for event in sub:
        if seen is not None:
            seen.append(event)
        if event.type in types:
            return event
    raise AssertionError(f"stream ended before {types}")


async def settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0.02)


def one_call_kit(world: World, *tail: Any) -> Kit:
    return world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), *tail)


async def test_approve_through_the_facade_runs_the_tool_and_finishes_the_run() -> None:
    world = World()
    app = app_for(world, one_call_kit(world, reply("fin")))
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        request = await asyncio.wait_for(until(sub, "approval.requested"), 5)
        assert app.decide(Decide(approval_id=request.approval.approval_id, decision="approve"))
        seen: list[Any] = []
        await asyncio.wait_for(until(sub, "run.finished", seen=seen), 5)
    kinds = [e.type for e in seen]
    assert kinds[0] == "approval.resolved" and "tool.finished" in kinds
    assert world.repo.records[request.approval.approval_id].state == "EXECUTED"
    assert world.repo.records[request.approval.approval_id].delivered is True


async def test_double_click_is_one_decision() -> None:
    world = World()
    app = app_for(world, one_call_kit(world, reply("fin")))
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        aid = (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
        first = app.decide(Decide(approval_id=aid, decision="approve"))
        second = app.decide(Decide(approval_id=aid, decision="approve"))
        await asyncio.wait_for(until(sub, "run.finished"), 5)
    assert (first, second) == (True, False)
    assert [t[2] for t in world.repo.transitions] == ["APPROVED", "CONSUMED", "EXECUTED"]


async def test_a_refused_edit_leaves_the_approval_pending_so_the_user_can_try_again() -> None:
    world = World()
    app = app_for(world, one_call_kit(world, reply("fin")))
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        aid = (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
        app.decide(Decide(approval_id=aid, decision="edit", edited_args={"todos": "nonsense"}))
        note = await asyncio.wait_for(until(sub, "notification"), 5)
        assert "Edit not accepted" in note.text
        await settle()
        assert world.repo.records[aid].state == "PENDING"
        assert app.decide(
            Decide(approval_id=aid, decision="reject", comment="no")
        )  # still answerable
        await asyncio.wait_for(until(sub, "run.finished"), 5)
    assert world.repo.records[aid].state == "REJECTED"


async def test_a_late_approval_is_reported_expired_and_the_run_still_finishes() -> None:
    world = World()
    app = app_for(world, one_call_kit(world, reply("fin")))
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        aid = (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
        world.clock.advance(timedelta(hours=9))
        app.decide(Decide(approval_id=aid, decision="approve"))
        seen: list[Any] = []
        await asyncio.wait_for(until(sub, "run.finished", seen=seen), 5)
    assert "approval.expired" in [e.type for e in seen]
    assert world.repo.records[aid].state == "EXPIRED"


async def test_two_approvals_decided_back_to_back_finish_the_run_exactly_once() -> None:
    world = World()
    kit = world.kit(
        reply(
            "go",
            calls=[tool_call("write_todos", TODO_A, "t1"), tool_call("write_todos", TODO_B, "t2")],
        ),
        reply("both"),
    )
    app = app_for(world, kit)
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        ids: list[str] = []
        while len(ids) < 2:
            event = await asyncio.wait_for(until(sub, "approval.requested"), 5)
            ids.append(event.approval.approval_id)
        for aid in ids:  # no waiting in between: the stretches must serialize, not race
            assert app.decide(Decide(approval_id=aid, decision="approve"))
        seen: list[Any] = []
        await asyncio.wait_for(until(sub, "run.finished", seen=seen), 5)
        await settle()
        assert sub.pending() == 0  # one RunFinished, nothing after it
    assert [r.state for r in world.repo.records.values()] == ["EXECUTED", "EXECUTED"]
    assert [e.type for e in seen].count("run.finished") == 1


async def test_cancelling_a_suspended_run_cancels_its_approval_and_forgets_it() -> None:
    world = World()
    app = app_for(world, one_call_kit(world))
    with app.bus.subscribe() as sub:
        run_id = app.submit_message(SubmitMessage(text="go"))
        aid = (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
        assert await app.cancel(run_id) is True
        await asyncio.wait_for(until(sub, "run.cancelled"), 5)
    assert world.repo.records[aid].state == "CANCELLED"
    assert app.decide(Decide(approval_id=aid, decision="approve")) is False
    assert await app.cancel(run_id) is False


async def test_quitting_the_app_keeps_pending_approvals_for_the_next_start() -> None:
    """aclose() used to cancel the run's approvals, so a quit destroyed them."""
    world = World()
    app = app_for(world, one_call_kit(world))
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        aid = (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
        await app.aclose()  # straight away: the stretch may still be winding down
    assert world.repo.records[aid].state == "PENDING"
    assert world.repo.records[aid].delivered is False


# ---- crash recovery (ARCHITECTURE 11.4 step 2) ------------------------------------------------
async def first_process(
    world: World, *script: Any, calls: Sequence[Any] | None = None
) -> list[str]:
    """Run until the approvals are raised, then 'kill' the process by abandoning it."""
    kit = world.kit(
        reply("go", calls=list(calls or [tool_call("write_todos", TODO_A, "t1")])), *script
    )
    app = app_for(world, kit)
    ids: list[str] = []
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="go"))
        for _ in calls or [0]:
            ids.append(
                (await asyncio.wait_for(until(sub, "approval.requested"), 5)).approval.approval_id
            )
    await settle()  # a real crash is after the pause is checkpointed; see the test below for before
    await app.aclose()
    return ids


async def test_kill_between_approve_and_consume_resumes_and_executes_once() -> None:
    world = World()
    (aid,) = await first_process(world)
    await world.broker().resolve(
        aid, decision="approve"
    )  # decided; the process died before delivery
    assert world.repo.records[aid].state == "APPROVED"

    app = app_for(world, world.kit(reply("done after crash")))  # a fresh process
    with app.bus.subscribe() as sub:
        assert await app.recover() == 1
        seen: list[Any] = []
        await asyncio.wait_for(until(sub, "run.finished", seen=seen), 5)
    assert world.repo.records[aid].state == "EXECUTED"
    assert world.repo.records[aid].delivered is True
    assert [t[2] for t in world.repo.transitions].count("CONSUMED") == 1  # executed once
    # recovering again, e.g. the next start, finds nothing left to do
    assert await app_for(world, world.kit(reply("x"))).recover() == 0


async def test_a_pending_approval_from_before_the_restart_comes_back_as_a_dialog() -> None:
    world = World()
    (aid,) = await first_process(world)

    app = app_for(world, world.kit(reply("done")))
    with app.bus.subscribe() as sub:
        assert await app.recover() == 1
        event = await asyncio.wait_for(until(sub, "approval.requested"), 5)
        assert event.approval.approval_id == aid
        assert event.run_id == world.repo.records[aid].run_id
        assert app.decide(Decide(approval_id=aid, decision="approve"))
        await asyncio.wait_for(until(sub, "run.finished"), 5)
    assert world.repo.records[aid].state == "EXECUTED"


async def test_an_approval_that_expired_while_closed_is_not_resumed_unattended() -> None:
    world = World()
    (aid,) = await first_process(world)
    world.clock.advance(timedelta(hours=9))
    await world.broker().expire_stale()  # what reconcile_storage does at startup

    app = app_for(world, world.kit(reply("must not be used")))
    with app.bus.subscribe() as sub:
        assert await app.recover() == 0
        note = await asyncio.wait_for(until(sub, "notification"), 5)
    assert "expired while the app was closed" in note.text
    assert world.repo.records[aid].delivered is True  # abandoned, not retried every start
    assert world.repo.records[aid].state == "EXPIRED"


async def test_a_rejection_made_before_the_crash_is_delivered_on_recovery() -> None:
    world = World()
    (aid,) = await first_process(world)
    await world.broker().resolve(aid, decision="reject", comment="no")

    kit = world.kit(reply("ok, skipped"))
    app = app_for(world, kit)
    with app.bus.subscribe() as sub:
        await app.recover()
        await asyncio.wait_for(until(sub, "run.finished"), 5)
    assert world.repo.records[aid].state == "REJECTED" and world.repo.records[aid].delivered


async def test_a_decision_for_a_run_that_was_never_paused_is_not_silently_swallowed() -> None:
    """Crash between writing the approval row and checkpointing the pause."""
    world = World()
    kit = world.kit(reply("never used"))
    with bind(run_id="run_x", conversation_id="conv_x", thread_id="thr_never"):
        ticket = await world.broker().request(
            tool="write_todos",
            tool_version="1",
            principal=Principal.AGENT,
            risk=RiskClass.R1,
            args={"todos": []},
            run_id="run_x",
            tool_call_id="t1",
            tainted=False,
        )
    aid = ticket.view.approval_id
    await world.broker().resolve(aid, decision="approve")

    app = app_for(world, kit)
    with app.bus.subscribe() as sub:
        await app.recover()
        note = await asyncio.wait_for(until(sub, "notification"), 5)
    assert "not executed" in note.text
    assert world.repo.records[aid].state == "INVALIDATED"
    assert world.repo.records[aid].delivered is True
