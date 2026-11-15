import pytest

from backend.services.graph.core.deep_agent import _user_oauth_signal, should_route_deep_for_oauth
from backend.services.integrations.core.calendar.events import (
    build_event_body,
    build_event_window,
    calendar_scope_granted,
    format_when_label,
    missing_calendar_fields,
    split_event_ids,
)


def test_calendar_scope_granted() -> None:
    assert calendar_scope_granted(["https://www.googleapis.com/auth/calendar.events"])
    assert not calendar_scope_granted(["https://www.googleapis.com/auth/gmail.readonly"])


def test_oauth_continuation_routes_deep() -> None:
    hist = [
        {"role": "assistant", "content": "Reply **yes** if you want to connect now."},
    ]
    assert should_route_deep_for_oauth("yes", hist)
    assert _user_oauth_signal("done") == "done"


def test_missing_fields() -> None:
    assert missing_calendar_fields("", "2026-01-01T10:00") == ["title"]
    assert missing_calendar_fields("Meet", "") == ["start datetime (start_iso)"]
    assert not missing_calendar_fields("Meet", "2026-01-01T10:00")


def test_build_event_body_kolkata_local_hour() -> None:
    body = build_event_body(
        title="Reminder",
        start_iso="2026-11-17T13:00:00",
        time_zone="Asia/Kolkata",
    )
    assert body["start"]["dateTime"] == "2026-11-17T13:00:00"
    assert body["start"]["timeZone"] == "Asia/Kolkata"


def test_build_event_body_default_end() -> None:
    body = build_event_body(title="Standup", start_iso="2026-06-01T09:00:00", time_zone="UTC")
    assert body["summary"] == "Standup"
    assert body["start"]["dateTime"] == "2026-06-01T09:00:00"
    assert body["end"]["dateTime"] == "2026-06-01T09:30:00"


def test_build_event_window_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="end must be after start"):
        build_event_window(
            "2026-06-01T10:00:00",
            "2026-06-01T09:00:00",
            "UTC",
        )


def test_build_event_body_raises_on_missing() -> None:
    with pytest.raises(ValueError, match="missing: title"):
        build_event_body(title="", start_iso="2026-01-01T10:00", time_zone="UTC")


def test_build_event_all_day() -> None:
    body = build_event_body(title="Off", start_iso="2026-06-01", all_day=True, time_zone="UTC")
    assert body["start"] == {"date": "2026-06-01"}
    assert body["end"] == {"date": "2026-06-02"}


def test_format_when_label() -> None:
    assert (
        format_when_label("2026-11-16T13:00:00+05:30", "Asia/Kolkata")
        == "Mon 16 Nov 2026, 1:00 PM IST"
    )
    assert format_when_label("2026-06-01") == "Mon 01 Jun 2026"


def test_split_event_ids() -> None:
    assert split_event_ids("a,b; c") == ["a", "b", "c"]


def test_build_event_recurrence_attendees() -> None:
    body = build_event_body(
        title="Standup",
        start_iso="2026-06-01T09:00:00",
        time_zone="UTC",
        recurrence="FREQ=WEEKLY;BYDAY=MO",
        attendees="a@example.com, b@example.com",
        reminders_minutes=10,
    )
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO"]
    assert len(body["attendees"]) == 2
    assert body["reminders"]["overrides"][0]["minutes"] == 10