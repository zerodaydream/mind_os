from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from opsagent.agents import CALENDAR, ORCHESTRATOR_TOOLS, RunLimits
from opsagent.application import Application, EventBus
from opsagent.domain import Pricing, ReasoningEffort, events
from opsagent.domain.commands import Decide, SubmitMessage
from opsagent.infra import SystemClock, new_id
from tests.support.agent_kit import Kit, make_kit
from tests.support.scripted_model import ScriptedChatModel, reply, tool_call

SEARCH = tool_call("calendar.search_events", {"start": "2026-11-23", "end": "2026-11-23"}, "cs1")


def delegate(goal: str = "What is on Monday?", call_id: str = "d1", **extra: Any) -> dict[str, Any]:
    return tool_call("delegate", {"subagent": "calendar", "goal": goal, **extra}, call_id)


def happy_kit(**kw: Any) -> Kit:
    orchestrator = ScriptedChatModel(
        script=[
            reply(
                "Checking.",
                thinking="plan the lookup",
                calls=[
                    tool_call(
                        "write_todos",
                        {"todos": [{"id": "1", "text": "Check Monday", "status": "in_progress"}]},
                        "t1",
                    ),
                    delegate(),
                ],
                tokens=(100, 30, 12),
            ),
            reply("You have Standup on Monday at 09:30 IST.", tokens=(200, 20, 0)),
        ]
    )
    subagent = ScriptedChatModel(
        script=[
            reply(thinking="SECRET-SUB-THOUGHT", calls=[SEARCH], tokens=(50, 10, 5)),
            reply("Standup, Monday 09:30 IST (event e1).", tokens=(80, 15, 0)),
        ]
    )
    return make_kit(orchestrator, subagent, **kw)


def kinds(evts: list[Any]) -> list[str]:
    return [e.type for e in evts]


async def test_end_to_end_delegation_streams_ordered_events() -> None:
    kit = happy_kit()
    evts = await kit.collect(kit.spec("what's on Monday?"))
    seen = kinds(evts)
    for expected in (
        "thinking.delta",
        "thinking.complete",
        "assistant.message.complete",
        "tool.started",
        "plan.updated",
        "subagent.started",
        "subagent.finished",
        "tool.finished",
        "usage.updated",
    ):
        assert expected in seen, expected
    assert seen.index("subagent.started") < seen.index("subagent.finished")
    final = [e for e in evts if isinstance(e, events.AssistantMessageComplete)][-1]
    assert final.text == "You have Standup on Monday at 09:30 IST."
    finished = next(e for e in evts if isinstance(e, events.SubagentFinished))
    assert finished.status == "success"

    # The calendar tool call is attributed to the subagent; orchestrator calls are not.
    started = {e.tool: e for e in evts if isinstance(e, events.ToolCallStarted)}
    assert started["calendar.search_events"].subagent_id is not None
    assert started["delegate"].subagent_id is None
    assert all(e.run_id == "run_1" for e in evts)


async def test_subagent_thoughts_and_history_never_reach_the_orchestrator() -> None:
    kit = happy_kit()
    evts = await kit.collect(kit.spec())
    blob = " ".join(e.model_dump_json() for e in evts)
    assert "SECRET-SUB-THOUGHT" not in blob
    assert "plan the lookup" in blob  # the orchestrator's own thought summary is shown
    # Orchestrator history holds only its own messages and the delegate result.
    state = await kit.runtime.graph().aget_state({"configurable": {"thread_id": "thr_1"}})
    texts = " ".join(str(m.content) for m in state.values["messages"])
    assert "SECRET-SUB-THOUGHT" not in texts and "calendar.search_events" not in texts
    # And the subagent saw only the brief, not the user's message.
    first_prompt = " ".join(str(m.content) for m in kit.models.subagent.seen[0])
    assert "Task: What is on Monday?" in first_prompt and "what's on Monday" not in first_prompt


async def test_delegate_result_carries_code_collected_evidence() -> None:
    kit = happy_kit()
    await kit.collect(kit.spec())
    last_call = kit.models.orchestrator.seen[-1]
    delegate_msg = next(m for m in last_call if isinstance(m, ToolMessage) and m.name == "delegate")
    assert '"status":"success"' in str(delegate_msg.content)
    assert '"kind":"calendar_event","id":"e1"' in str(delegate_msg.content)
    assert str(delegate_msg.content).startswith('<untrusted source="delegate">')


async def test_model_visible_tools_equal_the_allowlists() -> None:
    kit = happy_kit()
    await kit.collect(kit.spec())
    assert set(kit.models.orchestrator.bound_tool_names) == ORCHESTRATOR_TOOLS
    assert set(kit.models.subagent.bound_tool_names) == CALENDAR.tools
    # No framework filesystem or shell tools, and no delegate for subagents (depth is one).
    banned = {"write_file", "edit_file", "read_file", "execute", "ls", "glob", "grep", "task"}
    assert not banned & set(kit.models.orchestrator.bound_tool_names)
    assert "delegate" not in kit.models.subagent.bound_tool_names


async def test_forced_calls_to_ungranted_tools_are_rejected_with_no_effect() -> None:
    orchestrator = ScriptedChatModel(
        script=[
            reply(
                calls=[
                    tool_call(
                        "calendar.search_events", {"start": "2026-11-23", "end": "2026-11-23"}, "x1"
                    ),
                    tool_call("write_file", {"file_path": "/tmp/pwn", "content": "x"}, "x2"),
                    tool_call("execute", {"command": "id"}, "x3"),
                ]
            ),
            reply("Cannot do that."),
        ]
    )
    kit = make_kit(orchestrator)
    evts = await kit.collect(kit.spec())
    finished = [e for e in evts if isinstance(e, events.ToolCallFinished)]
    assert [e.status for e in finished] == ["rejected"] * 3
    assert kit.calendar.calls == []
    assert kit.audit.types() == ["tool.denied"] * 3
    # A subagent cannot spawn a subagent either.
    sub = ScriptedChatModel(script=[reply(calls=[delegate(call_id="n1")]), reply("no")])
    kit2 = make_kit(ScriptedChatModel(script=[reply(calls=[delegate()]), reply("done")]), sub)
    await kit2.collect(kit2.spec())
    assert "not_permitted" in kit2.audit.rows[0]["data"]["error_code"]


async def test_unknown_subagent_is_a_tool_error_the_model_can_read() -> None:
    orchestrator = ScriptedChatModel(
        script=[
            reply(calls=[tool_call("delegate", {"subagent": "mail", "goal": "x"}, "d9")]),
            reply("ok"),
        ]
    )
    kit = make_kit(orchestrator)
    await kit.collect(kit.spec())
    msg = next(m for m in kit.models.orchestrator.seen[-1] if isinstance(m, ToolMessage))
    assert "Unknown subagent 'mail'. Available: calendar." in str(msg.content)


async def test_tool_call_budget_is_shared_and_refuses_after_exhaustion() -> None:
    kit = happy_kit(limits=RunLimits(max_tool_calls=2))  # write_todos + delegate; subagent starves
    evts = await kit.collect(kit.spec())
    search_end = next(
        e
        for e in evts
        if isinstance(e, events.ToolCallFinished) and e.tool == "calendar.search_events"
    )
    assert search_end.status == "rejected"
    assert "budget_exhausted" in [r["data"]["error_code"] for r in kit.audit.rows]
    sub = next(e for e in evts if isinstance(e, events.SubagentFinished))
    assert sub.status == "partial"  # a tool was refused during the work


async def test_step_limit_ends_partial_and_the_thread_is_repaired_for_the_next_turn() -> None:
    looping = ScriptedChatModel(
        script=[], fallback=reply(calls=[tool_call("write_todos", {"todos": []}, "loop")])
    )
    kit = make_kit(looping, limits=RunLimits(max_steps=3))
    evts = await kit.collect(kit.spec("go"))
    assert isinstance(evts[-1], events.RunFinished) and evts[-1].summary.startswith("partial")
    notice = next(e for e in evts if isinstance(e, events.Notification))
    assert "step limit of 3" in notice.text and notice.level == "warning"

    looping.load(fallback=reply("Back again."))
    await kit.collect(kit.spec("continue"))
    history = looping.seen[-1]
    calls = {c["id"] for m in history if isinstance(m, AIMessage) for c in m.tool_calls}
    answers = {m.tool_call_id for m in history if isinstance(m, ToolMessage)}
    assert calls <= answers  # no dangling tool call reaches the provider


async def test_deadline_ends_partial() -> None:
    slow = ScriptedChatModel(script=[reply("late")], delay=1.0)
    kit = make_kit(slow, limits=RunLimits(deadline_s=0.05))
    evts = await kit.collect(kit.spec())
    assert isinstance(evts[-1], events.RunFinished) and "deadline" in evts[-1].summary


async def test_model_errors_surface_to_the_supervisor() -> None:
    class Boom(ScriptedChatModel):
        def _next(self, messages: Any) -> AIMessage:
            raise ConnectionError("provider down")

    kit = make_kit(Boom(script=[]))
    with pytest.raises(ConnectionError, match="provider down"):
        await kit.collect(kit.spec())
    assert kit.runtime._hub.get("run_1") is None  # state is always released


async def test_cancelling_the_consumer_stops_the_worker_and_releases_state() -> None:
    slow = ScriptedChatModel(script=[reply("never")], delay=5.0)
    kit = make_kit(slow)
    task = asyncio.create_task(kit.collect(kit.spec()))
    await asyncio.sleep(0.1)
    state = kit.runtime._hub.get("run_1")
    assert state is not None and state.task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert state.task.done() and kit.runtime._hub.get("run_1") is None


async def test_runtime_cancel_cancels_the_worker() -> None:
    slow = ScriptedChatModel(script=[reply("never")], delay=5.0)
    kit = make_kit(slow)
    task = asyncio.create_task(kit.collect(kit.spec()))
    await asyncio.sleep(0.1)
    await kit.runtime.cancel("run_1")
    # Worker cancellation ends the stream without a terminal event; the facade adds one.
    assert await asyncio.wait_for(task, 2) == []


async def test_effort_override_applies_to_the_orchestrator_only() -> None:
    kit = happy_kit()
    await kit.collect(kit.spec(effort=ReasoningEffort.MINIMAL))
    assert ("orchestrator", ReasoningEffort.MINIMAL) in {
        (r.value, e) for r, e in kit.models.requested
    }
    assert ("subagent", ReasoningEffort.LOW) in {(r.value, e) for r, e in kit.models.requested}


async def test_usage_is_recorded_per_call_and_totals_are_reported() -> None:
    kit = happy_kit(pricing=Pricing(input_per_mtok=1.0, output_per_mtok=4.0))
    evts = await kit.collect(kit.spec())
    assert [r["role"] for r in kit.usage.rows].count("orchestrator") == 2
    assert [r["role"] for r in kit.usage.rows].count("subagent") == 2
    total = [e for e in evts if isinstance(e, events.UsageUpdated)][-1]
    assert (total.input_tokens, total.output_tokens, total.reasoning_tokens) == (430, 75, 17)
    assert total.cost_usd == pytest.approx((430 * 1.0 + 75 * 4.0) / 1_000_000)
    assert total.scope == "session"


async def test_cost_is_unreported_without_pricing() -> None:
    kit = happy_kit()
    evts = await kit.collect(kit.spec())
    assert [e for e in evts if isinstance(e, events.UsageUpdated)][-1].cost_usd is None


async def test_follow_up_in_the_same_thread_sees_history() -> None:
    kit = happy_kit()
    await kit.collect(kit.spec("first"))
    kit.models.orchestrator.load(reply("second answer"))
    await kit.collect(kit.spec("second"))
    last = kit.models.orchestrator.seen[-1]
    assert any("first" in str(m.content) for m in last)


async def test_thinking_is_not_written_to_audit_rows() -> None:
    kit = happy_kit()
    await kit.collect(kit.spec())
    assert "plan the lookup" not in str(kit.audit.rows)
    assert "SECRET-SUB-THOUGHT" not in str(kit.audit.rows)


def test_resume_is_available_when_approvals_exist() -> None:
    kit = make_kit(ScriptedChatModel(script=[]))
    stream = kit.runtime.resume("thr", Decide(approval_id="apr_1", decision="reject"))
    assert hasattr(stream, "__aiter__")


async def _until(sub: Any, stop: str) -> list[Any]:
    seen: list[Any] = []
    async for event in sub:
        seen.append(event)
        if event.type == stop:
            return seen
    return seen


async def test_through_the_facade_a_run_ends_with_exactly_one_terminal_event() -> None:
    kit = happy_kit()
    app = Application(runtime=kit.runtime, bus=EventBus(SystemClock()), new_id=new_id)
    with app.bus.subscribe() as sub:
        run_id = app.submit_message(SubmitMessage(text="what's on Monday?"))
        seen = await asyncio.wait_for(_until(sub, "run.finished"), 10)
    assert seen[0].type == "run.started" and seen[-1].type == "run.finished"
    assert sum(e.type.startswith("run.") for e in seen) == 2  # started + finished
    assert {e.run_id for e in seen} == {run_id}
    assert seen[-1].seq > seen[0].seq  # the bus stamped ordering
    assert kit.runtime._hub.get(run_id) is None


async def test_through_the_facade_cancel_publishes_run_cancelled() -> None:
    kit = make_kit(ScriptedChatModel(script=[reply("never")], delay=5.0))
    app = Application(runtime=kit.runtime, bus=EventBus(SystemClock()), new_id=new_id)
    with app.bus.subscribe() as sub:
        run_id = app.submit_message(SubmitMessage(text="slow"))
        await asyncio.sleep(0.2)
        assert await app.cancel(run_id) is True
        seen = await asyncio.wait_for(_until(sub, "run.cancelled"), 5)
    assert seen[-1].type == "run.cancelled"
    assert kit.runtime._hub.get(run_id) is None


async def test_through_the_facade_model_failure_becomes_run_failed_without_secrets() -> None:
    class Boom(ScriptedChatModel):
        def _next(self, messages: Any) -> AIMessage:
            raise ConnectionError("down: key AIza" + "z" * 35)

    kit = make_kit(Boom(script=[]))
    app = Application(runtime=kit.runtime, bus=EventBus(SystemClock()), new_id=new_id)
    with app.bus.subscribe() as sub:
        app.submit_message(SubmitMessage(text="x"))
        seen = await asyncio.wait_for(_until(sub, "run.failed"), 5)
    assert seen[-1].code == "ConnectionError" and "AIza" not in seen[-1].message
