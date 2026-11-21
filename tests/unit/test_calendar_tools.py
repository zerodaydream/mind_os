from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from opsagent.domain import CalendarEvent, ToolInvocation
from opsagent.tools import (
    ReadOnlyPolicy,
    ToolExecutionEngine,
    ToolRegistry,
    calendar_specs,
)
from opsagent.tools.calendar import parse_when
from tests.support.fakes import MemoryArtifacts, MemoryAudit, MemoryCalendar

IST = ZoneInfo("Asia/Kolkata")


def event(event_id: str, title: str, start: datetime, hours: float = 1, **kw: Any) -> CalendarEvent:
    return CalendarEvent(
        id=event_id, title=title, start=start, end=start + timedelta(hours=hours), **kw
    )


def make(events: list[CalendarEvent]) -> tuple[ToolExecutionEngine, MemoryCalendar]:
    reader = MemoryCalendar(events)
    registry = ToolRegistry(calendar_specs(reader, IST))
    engine = ToolExecutionEngine(
        registry=registry,
        grants={"calendar": registry.names()},
        policy=ReadOnlyPolicy(),
        audit=MemoryAudit(),
        artifacts=MemoryArtifacts(),
        sleep=lambda s: _nothing(),
    )
    return engine, reader


async def _nothing() -> None:
    return None


async def call(engine: ToolExecutionEngine, tool: str, args: dict[str, Any]) -> Any:
    return await engine.execute(
        ToolInvocation(tool=tool, args=args, agent="calendar", tool_call_id="c1")
    )


def payload(result: Any) -> dict[str, Any]:
    body = result.content.split("\n", 1)[1].rsplit("\n", 1)[0]  # strip the untrusted wrapper
    return json.loads(body)


def test_bare_date_means_local_midnight_and_end_means_end_of_day() -> None:
    assert parse_when("2026-11-23", IST, is_end=False) == datetime(2026, 11, 23, tzinfo=IST)
    assert parse_when("2026-11-23", IST, is_end=True) == datetime(2026, 11, 24, tzinfo=IST)


def test_naive_datetime_uses_user_zone_and_offsets_are_kept() -> None:
    assert parse_when("2026-11-23T09:30", IST, is_end=False).utcoffset() == timedelta(hours=5.5)
    assert parse_when("2026-11-23T09:30Z", IST, is_end=False).utcoffset() == timedelta(0)


def test_garbage_dates_are_rejected() -> None:
    with pytest.raises(ValueError, match="not a valid ISO"):
        parse_when("next tuesday", IST, is_end=False)


async def test_search_renders_user_zone_weekday_and_evidence() -> None:
    standup = event("e1", "Standup", datetime(2026, 11, 23, 4, 0, tzinfo=UTC))  # 09:30 IST Monday
    engine, reader = make([standup, event("e2", "Far", datetime(2026, 12, 30, tzinfo=UTC))])
    result = await call(
        engine, "calendar.search_events", {"start": "2026-11-23", "end": "2026-11-23"}
    )
    assert result.status == "ok"
    body = payload(result)
    assert body["timezone"] == "Asia/Kolkata" and body["count"] == 1
    shown = body["events"][0]
    assert shown["start"] == "2026-11-23T09:30:00+05:30" and shown["weekday"] == "Monday"
    assert [e.id for e in result.evidence] == ["e1"]
    assert reader.calls[0]["end"] == datetime(2026, 11, 24, tzinfo=IST)  # whole day
    assert result.content.startswith('<untrusted source="calendar.search_events">')


async def test_limit_reports_truncation_without_leaking_the_probe_row() -> None:
    base = datetime(2026, 11, 23, 4, tzinfo=UTC)
    engine, reader = make([event(f"e{i}", f"T{i}", base + timedelta(minutes=i)) for i in range(5)])
    result = await call(
        engine,
        "calendar.search_events",
        {"start": "2026-11-23", "end": "2026-11-24", "limit": 3},
    )
    body = payload(result)
    assert (body["count"], body["truncated"]) == (3, True)
    assert reader.calls[0]["limit"] == 4


@pytest.mark.parametrize(
    "args",
    [
        {"start": "2026-11-24", "end": "2026-11-23"},  # backwards
        {"start": "2026-01-01", "end": "2026-12-31"},  # too long
    ],
)
async def test_bad_ranges_are_invalid_arguments(args: dict[str, str]) -> None:
    engine, reader = make([])
    result = await call(engine, "calendar.search_events", args)
    assert result.error_code == "invalid_arguments"
    assert reader.calls == []


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-01", "", "x" * 41])
async def test_unparseable_dates_fail_validation_before_the_adapter(bad: str) -> None:
    engine, reader = make([])
    result = await call(engine, "calendar.search_events", {"start": bad, "end": "2026-11-23"})
    assert result.error_code == "invalid_arguments"
    assert reader.calls == []


async def test_transient_adapter_errors_are_retried() -> None:
    engine, reader = make([])
    reader.fail_times = 2
    result = await call(
        engine, "calendar.search_events", {"start": "2026-11-23", "end": "2026-11-24"}
    )
    assert result.status == "ok" and len(reader.calls) == 3


async def test_get_event_includes_bounded_description_and_handles_missing() -> None:
    long_text = "d" * 5000
    ev = event("e9", "Review", datetime(2026, 11, 23, 4, tzinfo=UTC), description=long_text)
    engine, _ = make([ev])
    found = await call(engine, "calendar.get_event", {"event_id": "e9"})
    body = payload(found)["event"]
    assert len(body["description"]) == 2000 and body["description_truncated"] is True
    missing = await call(engine, "calendar.get_event", {"event_id": "nope"})
    assert missing.error_code == "not_found"


def test_calendar_events_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="UTC offset"):
        CalendarEvent(id="x", title="t", start=datetime(2026, 1, 1), end=datetime(2026, 1, 2))
