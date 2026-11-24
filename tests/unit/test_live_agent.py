"""One real-model scenario through the whole stack. Opt in with MINDOS_TEST_LIVE=1."""

from __future__ import annotations

import os

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from mindos.agents import RunLimits, build_runtime
from mindos.bootstrap import build_container
from mindos.domain import ModelRole, RunSpec, events
from mindos.infra import SystemClock, load_settings, new_id, resolve_timezone
from mindos.integrations.calendar import FixtureCalendar, sample_events
from tests.support.fakes import MemoryArtifacts, MemoryAudit, MemoryUsage
from tests.support.policies import ReadOnlyPolicy

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("MINDOS_TEST_LIVE") != "1", reason="set MINDOS_TEST_LIVE=1"),
]


async def test_calendar_question_end_to_end() -> None:
    settings = load_settings()
    llm = build_container(settings).llm
    clock, tz = SystemClock(), resolve_timezone(settings.timezone)
    audit, usage = MemoryAudit(), MemoryUsage()
    runtime = build_runtime(
        models=llm,  # type: ignore[arg-type]
        calendar=FixtureCalendar(sample_events(clock.now(), tz)),
        tz=tz,
        audit=audit,
        artifacts=MemoryArtifacts(),
        usage=usage,
        pricing=settings.pricing(),
        policy=ReadOnlyPolicy(),
        checkpointer=InMemorySaver(),
        limits=RunLimits(deadline_s=180),
        clock=clock,
        new_id=new_id,
        efforts={role: settings.reasoning_for(role) for role in ModelRole},
    )
    spec = RunSpec(
        run_id="run_live",
        conversation_id="conv_live",
        thread_id="thr_live",
        user_text="What is on my calendar tomorrow? Ask your calendar subagent.",
    )
    seen = [e async for e in runtime.start(spec)]

    assert not any(isinstance(e, events.RunFailed) for e in seen)
    tools = [e.tool for e in seen if isinstance(e, events.ToolCallStarted)]
    assert "delegate" in tools and "calendar.search_events" in tools
    answer = " ".join(e.text for e in seen if isinstance(e, events.AssistantMessageComplete))
    assert "design review" in answer.lower()
    assert {r["role"] for r in usage.rows} >= {"orchestrator", "subagent"}
    assert "tool.executed" in audit.types()
