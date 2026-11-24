"""In-memory calendar for development and tests, used when Google Calendar is not configured."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from mindos.domain import CalendarEvent, EventDraft, EventPatch


class FixtureCalendar:
    """Implements `CalendarReader` and `CalendarWriter` over a list of events held in memory."""

    def __init__(self, events: list[CalendarEvent]) -> None:
        self._events = sorted(events, key=lambda e: e.start)
        self._by_key: dict[str, str] = {}  # request_key -> event id
        self._next = 0

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

    async def create_event(
        self, draft: EventDraft, *, request_key: str, notify: bool
    ) -> CalendarEvent:
        existing = await self.find_created(request_key)
        if existing is not None:
            return existing
        self._next += 1
        event = CalendarEvent(id=f"evt_new_{self._next}", **draft.model_dump())
        self._by_key[request_key] = event.id
        self._store([*self._events, event])
        return event

    async def find_created(self, request_key: str) -> CalendarEvent | None:
        event_id = self._by_key.get(request_key)
        return await self.get_event(event_id) if event_id else None

    async def update_event(
        self, event_id: str, patch: EventPatch, *, notify: bool
    ) -> CalendarEvent | None:
        current = await self.get_event(event_id)
        if current is None:
            return None
        changes = {k: v for k, v in patch.model_dump().items() if v is not None}
        updated = current.model_copy(update=changes)
        self._store([updated if e.id == event_id else e for e in self._events])
        return updated

    async def delete_event(self, event_id: str, *, notify: bool) -> bool:
        before = len(self._events)
        self._store([e for e in self._events if e.id != event_id])
        return len(self._events) < before

    def _store(self, events: list[CalendarEvent]) -> None:
        self._events = sorted(events, key=lambda e: e.start)


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
