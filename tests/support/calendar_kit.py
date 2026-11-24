"""The calendar tools on the real engine, broker, and policy, over the in-memory fixture."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from mindos.application.approval_broker import ApprovalBroker
from mindos.domain import (
    CalendarEvent,
    EventDraft,
    EventPatch,
    Principal,
    TaintLedger,
    ToolInvocation,
    ToolResult,
)
from mindos.integrations.calendar import FixtureCalendar, sample_events
from mindos.tools import ToolExecutionEngine, ToolRegistry, calendar_specs, calendar_write_specs
from mindos.tools.policy import PolicyConfig, PolicyEngine
from tests.support.fakes import (
    ManualClock,
    MemoryApprovalRepo,
    MemoryArtifacts,
    MemoryAudit,
    MemoryExecutionLog,
)

IST = ZoneInfo("Asia/Kolkata")


class FlakyCalendar(FixtureCalendar):
    """A calendar whose writes can fail after (or before) taking effect."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        super().__init__(events)
        self.fail_create: Exception | None = None
        self.create_attempts = 0

    async def create_event(
        self, draft: EventDraft, *, request_key: str, notify: bool
    ) -> CalendarEvent:
        self.create_attempts += 1
        event = await super().create_event(draft, request_key=request_key, notify=notify)
        if self.fail_create is not None:
            raise self.fail_create  # it happened, but the caller was never told
        return event


class Writes(FixtureCalendar):
    """Records `notify` flags next to the fixture's behaviour."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        super().__init__(events)
        self.notified: list[bool] = []

    async def update_event(
        self, event_id: str, patch: EventPatch, *, notify: bool
    ) -> CalendarEvent | None:
        self.notified.append(notify)
        return await super().update_event(event_id, patch, notify=notify)


class CalEnv:
    def __init__(
        self, *, config: PolicyConfig | None = None, calendar: FixtureCalendar | None = None
    ) -> None:
        self.clock = ManualClock()
        self.calendar = calendar or Writes(
            sample_events(datetime(2026, 11, 23, 9, tzinfo=IST), IST)
        )
        self.repo = MemoryApprovalRepo(self.clock)
        self.audit = MemoryAudit()
        self.taint = TaintLedger()
        self.events: list[Any] = []
        self._seq = 0
        broker = ApprovalBroker(
            repo=self.repo, audit=self.audit, new_id=self.new_id, clock=self.clock
        )
        registry = ToolRegistry(
            [
                *calendar_specs(self.calendar, IST),
                *calendar_write_specs(self.calendar, self.calendar, IST),
            ]
        )
        self.engine = ToolExecutionEngine(
            registry=registry,
            grants={"cal": registry.names()},
            policy=PolicyEngine(config or PolicyConfig()),
            audit=self.audit,
            artifacts=MemoryArtifacts(),
            broker=broker,
            taint=self.taint,
            executions=MemoryExecutionLog(),
            new_id=self.new_id,
        )
        self.broker = broker

    def new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:04d}"

    def inv(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        call_id: str = "c1",
        approval_id: str | None = None,
    ) -> ToolInvocation:
        return ToolInvocation(
            tool=tool,
            args=args,
            agent="cal",
            tool_call_id=call_id,
            run_id="run_1",
            principal=Principal.AGENT,
            approval_id=approval_id,
            can_pause=True,
        )

    async def call(self, tool: str, args: dict[str, Any], **kw: Any) -> ToolResult:
        return await self.engine.execute(self.inv(tool, args, **kw), emit=self.events.append)

    async def approved(self, tool: str, args: dict[str, Any], **kw: Any) -> ToolResult:
        """Propose, approve, then run the approved call. Returns the final result."""
        pending = await self.call(tool, args, **kw)
        assert pending.status == "pending_approval", pending.content
        aid = pending.approval_id or ""
        await self.broker.resolve(aid, decision="approve")
        return await self.call(tool, args, approval_id=aid, **kw)

    def only_approval(self) -> Any:
        (record,) = self.repo.records.values()
        return record
