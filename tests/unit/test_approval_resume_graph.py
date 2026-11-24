"""Pause and resume on the REAL LangGraph agent (scripted model, in-memory checkpointer).

These are the tests the mocked ones could not be: LangGraph re-runs a tool node from the top on
resume, runs parallel tool calls as parallel interrupts, and a new process must be able to
answer an approval that an old one raised.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.messages import ToolMessage

from mindos.domain import PolicyDecision, ReasoningEffort, bind, events
from mindos.domain.commands import Decide
from mindos.tools.policy import PolicyConfig, ToolPolicy
from tests.support.agent_kit import Kit
from tests.support.scripted_model import ScriptedChatModel, reply, tool_call
from tests.support.world import World

TODO_A = {"todos": [{"id": "1", "text": "first", "status": "pending"}]}
TODO_B = {"todos": [{"id": "2", "text": "second", "status": "pending"}]}


def requested(evts: list[Any]) -> list[str]:
    return [e.approval.approval_id for e in evts if isinstance(e, events.ApprovalRequested)]


def tool_messages(kit: Kit) -> list[ToolMessage]:
    """Tool results the orchestrator model was shown on its last call."""
    return [m for m in kit.models.orchestrator.seen[-1] if isinstance(m, ToolMessage)]


async def decide(
    world: World, kit: Kit, approval_id: str, decision: str = "approve", **kw: Any
) -> list[Any]:
    await world.broker().resolve(approval_id, decision=decision, **kw)
    command = Decide(approval_id=approval_id, decision=decision, comment=kw.get("comment"))  # type: ignore[arg-type]
    return [e async for e in kit.runtime.resume("thr_1", command)]


async def start(world: World, kit: Kit) -> tuple[list[Any], str]:
    evts = await kit.collect(kit.spec("do it", thread="thr_1"))
    ids = requested(evts)
    assert len(ids) == 1
    return evts, ids[0]


async def test_pause_then_approve_resumes_the_same_run_and_finishes() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("all done"))
    first, aid = await start(world, kit)
    assert "run.finished" not in [e.type for e in first]  # suspended, not finished

    out = await decide(world, kit, aid)
    kinds = [e.type for e in out]
    assert "approval.requested" not in kinds  # the replay must not re-announce the approval
    finished = [e for e in out if isinstance(e, events.ToolCallFinished)]
    assert [(f.tool, f.status) for f in finished] == [("write_todos", "ok")]
    assert finished[0].run_id == "run_1"  # run context is restored from the checkpoint
    assert any(isinstance(e, events.AssistantMessageComplete) and e.text == "all done" for e in out)

    assert list(world.repo.records) == [aid]  # no orphan approval from the replay
    assert world.repo.records[aid].state == "EXECUTED"
    assert world.repo.records[aid].actor == "USER"


async def test_a_resume_keeps_the_reasoning_effort_the_run_started_with() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("done"))
    evts = await kit.collect(kit.spec("do it", thread="thr_1", effort=ReasoningEffort.LOW))
    aid = requested(evts)[0]
    out = await decide(world, kit, aid)
    used = {e.reasoning_effort for e in out if isinstance(e, events.UsageUpdated)}
    assert used == {ReasoningEffort.LOW}  # the kit's default is HIGH


async def test_reject_gives_the_model_a_structured_not_executed_result() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("ok"))
    _, aid = await start(world, kit)
    out = await decide(world, kit, aid, "reject", comment="not now")
    assert tool_messages(kit)[0].content == "[rejected by user: not now; not executed]"
    assert world.repo.records[aid].state == "REJECTED"
    assert [e.status for e in out if isinstance(e, events.ToolCallFinished)] == ["rejected"]


async def test_a_resume_value_cannot_force_execution_of_an_unapproved_call() -> None:
    """Only the database decides. Resuming a still-pending approval executes nothing."""
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("ok"))
    _, aid = await start(world, kit)
    command = Decide(approval_id=aid, decision="approve")  # claims approval; the row says PENDING
    out = [e async for e in kit.runtime.resume("thr_1", command)]
    assert [e.status for e in out if isinstance(e, events.ToolCallFinished)] == ["rejected"]
    assert world.repo.records[aid].state == "PENDING"
    assert "approval_pending" in str(tool_messages(kit)[0].content)


async def test_an_approval_that_expired_while_paused_reaches_the_model_as_expired() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("ok"))
    _, aid = await start(world, kit)
    world.clock.advance(timedelta(hours=9))
    await world.broker().expire_stale()
    out = [e async for e in kit.runtime.resume("thr_1", Decide(approval_id=aid, decision="reject"))]
    assert "approval_expired" in str(tool_messages(kit)[0].content)
    assert any(e.type == "run.finished" or e.type == "assistant.message.complete" for e in out)


async def test_a_new_process_can_answer_an_approval_an_old_one_raised() -> None:
    world = World()
    old = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]))
    _, aid = await start(world, old)
    await world.broker().resolve(aid, decision="approve")

    fresh = world.kit(reply("done after restart"))  # new runtime, new engine, same database
    out = [
        e async for e in fresh.runtime.resume("thr_1", Decide(approval_id=aid, decision="approve"))
    ]
    assert world.repo.records[aid].state == "EXECUTED"
    assert len(world.repo.records) == 1
    assert any(
        isinstance(e, events.AssistantMessageComplete) and e.text == "done after restart"
        for e in out
    )


async def test_redelivering_a_delivered_decision_is_a_harmless_no_op() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("fin"))
    _, aid = await start(world, kit)
    await decide(world, kit, aid)
    again = [
        e async for e in kit.runtime.resume("thr_1", Decide(approval_id=aid, decision="approve"))
    ]
    assert again == []  # the checkpoint has nothing paused on it
    assert world.repo.records[aid].state == "EXECUTED"
    stale = [
        e
        async for e in kit.runtime.resume(
            "thr_1", Decide(approval_id="apr_nope", decision="approve")
        )
    ]
    assert stale == []


async def test_two_approvals_in_one_turn_resume_one_at_a_time() -> None:
    """Parallel tool calls pause as parallel interrupts; LangGraph needs the interrupt id."""
    world = World()
    kit = world.kit(
        reply(
            "go",
            calls=[tool_call("write_todos", TODO_A, "t1"), tool_call("write_todos", TODO_B, "t2")],
        ),
        reply("both done"),
    )
    evts = await kit.collect(kit.spec("do it", thread="thr_1"))
    first_id, second_id = requested(evts)

    after_first = await decide(world, kit, first_id)
    assert not any(
        isinstance(e, events.AssistantMessageComplete) for e in after_first
    )  # still waiting
    assert "approval.requested" not in [e.type for e in after_first]
    assert world.repo.records[first_id].state == "EXECUTED"
    assert world.repo.records[second_id].state == "PENDING"

    after_second = await decide(world, kit, second_id, "reject", comment="no")
    assert any(
        isinstance(e, events.AssistantMessageComplete) and e.text == "both done"
        for e in after_second
    )
    assert world.repo.records[second_id].state == "REJECTED"
    assert len(world.repo.records) == 2


async def test_a_gated_call_inside_a_subagent_is_deferred_and_the_run_finishes() -> None:
    """A subagent cannot pause. Its approval is queued; the run moves on and tells the user."""
    world = World()
    config = PolicyConfig(
        overrides=[ToolPolicy("calendar.search_events", PolicyDecision.REQUIRE_APPROVAL)]
    )
    search = tool_call(
        "calendar.search_events", {"start": "2026-11-23", "end": "2026-11-23"}, "cs1"
    )
    kit = world.kit(
        reply("go", calls=[tool_call("delegate", {"subagent": "calendar", "goal": "g"}, "d1")]),
        reply("reported"),
        subagent=ScriptedChatModel(script=[reply("", calls=[search]), reply("could not")]),
        config=config,
    )
    evts = await kit.collect(kit.spec("x", thread="thr_1"))
    (record,) = world.repo.records.values()
    assert (record.deferred, record.agent, record.state) == (True, "calendar", "PENDING")
    assert requested(evts) == [record.id]
    assert [e.approval.deferred for e in evts if isinstance(e, events.ApprovalRequested)] == [True]
    # nothing is paused on it: the orchestrator went on and wrote its final answer
    assert any(
        isinstance(e, events.AssistantMessageComplete) and e.text == "reported" for e in evts
    )
    delegated = str([m.content for m in tool_messages(kit)])
    assert "needs_input" in delegated and record.id in delegated
    assert world.repo.records[record.id].state == "PENDING"  # the call did not run

    await world.broker().resolve(record.id, decision="approve")
    result = await kit.runtime.run_approved(world.repo.records[record.id], lambda _e: None)
    assert result.status == "ok" and world.repo.records[record.id].state == "EXECUTED"


async def test_resume_binds_the_run_so_audit_and_budget_see_it() -> None:
    world = World()
    kit = world.kit(reply("go", calls=[tool_call("write_todos", TODO_A, "t1")]), reply("ok"))
    _, aid = await start(world, kit)
    with bind(run_id="someone_else"):  # ambient context must not leak into the resumed run
        await decide(world, kit, aid)
    executed = [r for r in world.audit.rows if r["event_type"] == "tool.executed"]
    assert executed and executed[0]["data"]["approval_id"] == aid
