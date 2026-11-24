"""Calendar model and read port. Adapters (fixture now, Google in Phase 6) live in integrations."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CalendarEvent(BaseModel):
    """An event. Titles, attendees, and descriptions are written by other people: untrusted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    calendar_id: str = "primary"
    title: str
    start: datetime
    end: datetime
    timezone: str = "UTC"  # IANA name; `start` and `end` carry the matching offset
    all_day: bool = False
    location: str | None = None
    attendees: tuple[str, ...] = ()
    description: str | None = Field(default=None, max_length=20_000)

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("calendar datetimes must carry a UTC offset")
        return value


class CalendarReader(Protocol):
    """Read-only calendar access. Write operations get a separate port in Phase 7."""

    async def search_events(
        self, *, start: datetime, end: datetime, query: str | None, limit: int
    ) -> list[CalendarEvent]:
        """Events overlapping [start, end), ordered by start, at most `limit`."""
        ...

    async def get_event(self, event_id: str) -> CalendarEvent | None: ...
