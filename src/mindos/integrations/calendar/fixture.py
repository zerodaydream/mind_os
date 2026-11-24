"""In-memory calendar for development until Google Calendar is connected (Phase 06)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from opsagent.domain import CalendarEvent


class FixtureCalendar:
    """Implements `CalendarReader` over a fixed list of events."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        self._events = sorted(events, key=lambda e: e.start)

    async def search_events(
        self, *, start: datetime, end: datetime, query: str | None, limit: int
    ) -> list[CalendarEvent]:
        needle = query.lower() if query else None
        hits = [
            e
            for e in self._events
            if e.start < end and e.end > start and (needle is None or needle in _haystack(e))
        ]
        return hits[:limit]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        return next((e for e in self._events if e.id == event_id), None)


def _haystack(event: CalendarEvent) -> str:
    return " ".join([event.title, event.location or "", *event.attendees]).lower()


def sample_events(now: datetime, tz: ZoneInfo) -> list[CalendarEvent]:
    """A week of plausible events around `now`, in the user's zone, so the agent has data."""
    today = now.astimezone(tz).date()

    def at(
        day_offset: int, hour: int, minute: int = 0, minutes: int = 30
    ) -> tuple[datetime, datetime]:
        begin = datetime.combine(today + timedelta(days=day_offset), time(hour, minute), tzinfo=tz)
        return begin, begin + timedelta(minutes=minutes)

    rows = [
        ("evt_standup", "Team standup", 0, 10, 0, 15, "Zoom", ("team@example.com",), None),
        ("evt_one_on_one", "1:1 with Priya", 0, 14, 0, 30, "Room 4", ("priya@example.com",), None),
        (
            "evt_design",
            "Design review",
            1,
            11,
            0,
            60,
            "Room 2",
            ("lee@example.com", "sam@example.com"),
            "Review the onboarding mockups. Bring feedback from last week.",
        ),
        ("evt_dentist", "Dentist", 2, 9, 30, 45, "Smile Clinic", (), None),
        (
            "evt_planning",
            "Quarterly planning",
            3,
            15,
            0,
            90,
            "Main hall",
            ("all@example.com",),
            None,
        ),
        ("evt_standup_2", "Team standup", 1, 10, 0, 15, "Zoom", ("team@example.com",), None),
    ]
    events = []
    for event_id, title, day, hour, minute, minutes, place, who, notes in rows:
        begin, finish = at(day, hour, minute, minutes)
        events.append(
            CalendarEvent(
                id=event_id,
                title=title,
                start=begin,
                end=finish,
                timezone=str(tz),
                location=place,
                attendees=who,
                description=notes,
            )
        )
    return events
