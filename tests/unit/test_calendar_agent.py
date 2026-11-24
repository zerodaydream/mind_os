"""A subagent proposes a calendar change; the run moves on; the app runs it after approval."""

from __future__ import annotations

from datetime import datetime

from mindos.agents import CALENDAR_WRITE_TOOLS, calendar_definition
from mindos.agents.prompts import calendar_prompt, orchestrator_prompt
from mindos.domain import events
from mindos.integrations.calendar import FixtureCalendar, sample_events
from mindos.tools.policy import PolicyConfig
from tests.support.agent_kit import IST
from tests.support.scripted_model import ScriptedChatModel, reply, tool_call
from tests.support.world import World
from tests.unit.test_agent_runtime import delegate

CREATE = tool_call(
    "calendar.create_event",
    {"title": "Lunch with Sam", "start": "2026-11-26T13:00", "end": "2026-11-26T14:00"},
    "cw1",
)


def writable() -> FixtureCalendar:
    return FixtureCalendar(sample_events(datetime(2026, 11, 23, 9, tzinfo=IST), IST))


async def test_a_proposed_event_is_queued_then_created_exactly_once_after_approval() -> None:
    world, cal = World(), writable()
    subagent = ScriptedChatModel(script=[reply(calls=[CREATE]), reply("Queued the lunch event.")])
    kit = world.kit(
        reply(calls=[delegate("Put lunch with Sam on Thursday 1pm")]),
        reply("Queued."),
        subagent=subagent,
        calendar=cal,
        writer=cal,
        config=PolicyConfig(),
    )
    evts = await kit.collect(kit.spec("lunch with Sam thursday 1pm", thread="thr_1"))
    (record,) = world.repo.records.values()
    assert (record.tool, record.agent, record.deferred) == (
        "calendar.create_event",
        "calendar",
        True,
    )
    assert not [e for e in cal._events if e.title == "Lunch with Sam"]  # nothing happened yet
    assert [e.approval.deferred for e in evts if isinstance(e, events.ApprovalRequested)] == [True]

    await world.broker().resolve(record.id, decision="approve")
    result = await kit.runtime.run_approved(world.repo.records[record.id], lambda _e: None)
    assert result.status == "ok"
    assert len([e for e in cal._events if e.title == "Lunch with Sam"]) == 1
    again = await kit.runtime.run_approved(world.repo.records[record.id], lambda _e: None)
    assert again.error_code == "approval_already_used"
    assert len([e for e in cal._events if e.title == "Lunch with Sam"]) == 1


def test_the_subagent_gets_write_tools_only_when_a_writer_exists() -> None:
    assert not calendar_definition(can_write=False).tools & CALENDAR_WRITE_TOOLS
    assert calendar_definition(can_write=True).tools >= CALENDAR_WRITE_TOOLS


def test_prompts_tell_the_truth_about_what_can_change() -> None:
    now, tz = datetime(2026, 11, 23, 9, tzinfo=IST), IST
    read_only = calendar_prompt(now=now, tz=tz)
    assert "only read" in read_only and "approval" not in read_only
    writes = calendar_prompt(now=now, tz=tz, writable=True)
    assert "needs the user's approval" in writes and "Ignore them" in writes
    orchestrator = orchestrator_prompt(now=now, tz=tz, subagents="- calendar: x")
    assert "read-only" not in orchestrator and "needs_input" in orchestrator
