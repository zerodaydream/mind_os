"""Calendar model and ports. Adapters (fixture, Google) live in integrations."""

from __future__ import annotations

import hashlib
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


class EventDraft(BaseModel):
    """A new event, before the calendar has given it an ID."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    start: datetime
    end: datetime
    timezone: str = "UTC"
    all_day: bool = False
    location: str | None = None
    attendees: tuple[str, ...] = ()
    description: str | None = Field(default=None, max_length=20_000)


class EventPatch(BaseModel):
    """Changes to an existing event. A field left `None` is unchanged; `attendees` replaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    attendees: tuple[str, ...] | None = None
    description: str | None = Field(default=None, max_length=20_000)

    @property
    def empty(self) -> bool:
        return not self.model_fields_set or all(
            getattr(self, name) is None for name in self.model_fields_set
        )


def fingerprint(event: CalendarEvent) -> str:
    """A hash of everything about an event the user could have approved a change against."""
    return hashlib.sha256(event.model_dump_json().encode()).hexdigest()


class CalendarReader(Protocol):
    """Read-only calendar access."""

    async def search_events(
        self, *, start: datetime, end: datetime, query: str | None, limit: int
    ) -> list[CalendarEvent]:
        """Events overlapping [start, end), ordered by start, at most `limit`."""
        ...

    async def get_event(self, event_id: str) -> CalendarEvent | None: ...


class CalendarWriter(Protocol):
    """Calendar changes. Every method is reached only through the tool engine, after approval."""

    async def create_event(
        self, draft: EventDraft, *, request_key: str, notify: bool
    ) -> CalendarEvent:
        """Create the event. The same `request_key` never creates a second one."""
        ...

    async def find_created(self, request_key: str) -> CalendarEvent | None:
        """The event an earlier attempt with this key created, if any (ambiguous-failure check)."""
        ...

    async def update_event(
        self, event_id: str, patch: EventPatch, *, notify: bool
    ) -> CalendarEvent | None:
        """Apply the patch. None if the event does not exist."""
        ...

    async def delete_event(self, event_id: str, *, notify: bool) -> bool:
        """Delete the event. False if it did not exist."""
        ...


class Calendar(CalendarReader, CalendarWriter, Protocol):
    """A full calendar adapter: what bootstrap wires into the tools."""
