import pytest

from backend.services.integrations.core.calendar_events import (
    build_event_body,
    build_event_window,
    missing_calendar_fields,
)


def test_missing_fields() -> None:
    assert missing_calendar_fields("", "2026-01-01T10:00") == ["title"]
    assert missing_calendar_fields("Meet", "") == ["start datetime (start_iso)"]
    assert not missing_calendar_fields("Meet", "2026-01-01T10:00")


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