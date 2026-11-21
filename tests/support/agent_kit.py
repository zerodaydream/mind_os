"""Builds a real `LangChainRuntime` on scripted models and in-memory ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from opsagent.agents import LangChainRuntime, RunLimits, build_runtime
from opsagent.domain import (
    AppEvent,
    CalendarEvent,
    ModelRole,
    Pricing,
    ReasoningEffort,
    RunSpec,
)
from opsagent.infra import new_id
from opsagent.tools import ReadOnlyPolicy
from tests.support.fakes import MemoryArtifacts, MemoryAudit, MemoryCalendar, MemoryUsage
from tests.support.scripted_model import ScriptedChatModel

IST = ZoneInfo("Asia/Kolkata")
MONDAY_0930 = datetime(2026, 11, 23, 4, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class FakeModels:
    def __init__(self, orchestrator: ScriptedChatModel, subagent: ScriptedChatModel) -> None:
        self.orchestrator, self.subagent = orchestrator, subagent
        self.requested: list[tuple[ModelRole, ReasoningEffort | None]] = []

    def chat(self, role: ModelRole, effort: ReasoningEffort | None) -> BaseChatModel:
        self.requested.append((role, effort))
        return self.orchestrator if role is ModelRole.ORCHESTRATOR else self.subagent

    def model_id(self, role: ModelRole) -> str:
        return f"fake-{role.value}"


@dataclass
class Kit:
    runtime: LangChainRuntime
    models: FakeModels
    audit: Any
    artifacts: Any
    usage: Any
    calendar: MemoryCalendar
    saver: Any
    counter: list[int] = field(default_factory=lambda: [0])

    def spec(
        self, text: str = "hi", *, thread: str = "thr_1", effort: ReasoningEffort | None = None
    ) -> RunSpec:
        self.counter[0] += 1
        return RunSpec(
            run_id=f"run_{self.counter[0]}",
            conversation_id="conv_1",
            thread_id=thread,
            user_text=text,
            effort=effort,
        )

    async def collect(self, spec: RunSpec) -> list[AppEvent]:
        return [event async for event in self.runtime.start(spec)]


def standup() -> CalendarEvent:
    return CalendarEvent(
        id="e1",
        title="Standup",
        start=MONDAY_0930,
        end=MONDAY_0930 + timedelta(minutes=30),
        timezone="Asia/Kolkata",
        attendees=("a@example.com",),
    )


def make_kit(
    orchestrator: ScriptedChatModel,
    subagent: ScriptedChatModel | None = None,
    *,
    limits: RunLimits | None = None,
    events: list[CalendarEvent] | None = None,
    pricing: Pricing | None = None,
    saver: Any = None,
    audit: Any = None,
    artifacts: Any = None,
    usage: Any = None,
) -> Kit:
    models = FakeModels(orchestrator, subagent or ScriptedChatModel(script=[]))
    audit, artifacts, usage = (
        audit or MemoryAudit(),
        artifacts or MemoryArtifacts(),
        usage or MemoryUsage(),
    )
    calendar = MemoryCalendar(events if events is not None else [standup()])
    saver = saver or InMemorySaver()
    runtime = build_runtime(
        models=models,
        calendar=calendar,
        tz=IST,
        audit=audit,
        artifacts=artifacts,
        usage=usage,
        pricing=pricing,
        policy=ReadOnlyPolicy(),
        checkpointer=saver,
        limits=limits or RunLimits(),
        clock=FixedClock(),
        new_id=new_id,
        efforts={
            ModelRole.ORCHESTRATOR: ReasoningEffort.HIGH,
            ModelRole.SUBAGENT: ReasoningEffort.LOW,
        },
    )
    return Kit(runtime, models, audit, artifacts, usage, calendar, saver)
