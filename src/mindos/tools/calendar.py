"""Calendar namespace: reads (Phase 04) and approval-gated writes (Phase 07).

Time is handled here, not by the model: tools take ISO 8601 text, resolve it in the user's zone,
and render every instant with its zone, offset, and weekday (ARCHITECTURE section 12.4).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from mindos.domain import (
    BoundState,
    CalendarEvent,
    CalendarReader,
    CalendarWriter,
    EventDraft,
    EventPatch,
    Evidence,
    RiskClass,
    ToolIdempotency,
    fingerprint,
)
from mindos.tools.spec import ToolContext, ToolError, ToolSpec

MAX_RANGE_DAYS = 92
MAX_ATTENDEES_SHOWN = 20
MAX_DESCRIPTION_CHARS = 2000
_WHEN_HELP = (
    "ISO 8601. A date like 2026-11-23 means the whole day in the user's time zone. "
    "A date-time may carry an offset; without one it is read in the user's time zone."
)


def parse_when(value: str, tz: ZoneInfo, *, is_end: bool) -> datetime:
    """Resolve model-supplied text to an aware datetime.

    A bare date starts at local midnight; as an end it means the end of that day (next midnight).
    """
    text = value.strip()
    try:
        if len(text) == 10:
            day = date.fromisoformat(text) + timedelta(days=1 if is_end else 0)
            return datetime.combine(day, time.min, tzinfo=tz)
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"'{value}' is not a valid ISO 8601 date or date-time") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)


def _check_format(value: str) -> str:
    parse_when(value, ZoneInfo("UTC"), is_end=False)
    return value


class SearchEventsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Annotated[str, StringConstraints(max_length=40)] = Field(description=_WHEN_HELP)
    end: Annotated[str, StringConstraints(max_length=40)] = Field(
        description="End of the range, exclusive for date-times. " + _WHEN_HELP
    )
    query: Annotated[str, StringConstraints(max_length=200)] | None = Field(
        default=None, description="Optional text to match in title, location, or attendees."
    )
    limit: int = Field(default=25, ge=1, le=50)

    _fmt = field_validator("start", "end")(_check_format)


class GetEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _render(event: CalendarEvent, tz: ZoneInfo, *, detail: bool) -> dict[str, Any]:
    start, end = event.start.astimezone(tz), event.end.astimezone(tz)
    out: dict[str, Any] = {
        "id": event.id,
        "calendar": event.calendar_id,
        "title": event.title,
        "weekday": start.strftime("%A"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "all_day": event.all_day,
        "location": event.location,
        "attendee_count": len(event.attendees),
        "attendees": list(event.attendees[:MAX_ATTENDEES_SHOWN]),
    }
    if detail:
        text = event.description or ""
        out["description"] = text[:MAX_DESCRIPTION_CHARS]
        out["description_truncated"] = len(text) > MAX_DESCRIPTION_CHARS
    return out


def calendar_specs(reader: CalendarReader, tz: ZoneInfo) -> tuple[ToolSpec, ToolSpec]:
    """Build the calendar tools. `reader` is the only adapter they can reach."""

    async def search(args: SearchEventsInput, ctx: ToolContext) -> dict[str, Any]:
        start = parse_when(args.start, tz, is_end=False)
        end = parse_when(args.end, tz, is_end=True)
        if end <= start:
            raise ToolError("`end` must be after `start`.", code="invalid_arguments")
        if end - start > timedelta(days=MAX_RANGE_DAYS):
            raise ToolError(
                f"Range is longer than {MAX_RANGE_DAYS} days. Search in smaller ranges.",
                code="invalid_arguments",
            )
        found = await reader.search_events(
            start=start, end=end, query=args.query, limit=args.limit + 1
        )
        shown = found[: args.limit]
        return {
            "timezone": str(tz),
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "count": len(shown),
            "truncated": len(found) > args.limit,
            "events": [_render(e, tz, detail=False) for e in shown],
        }

    async def get(args: GetEventInput, ctx: ToolContext) -> dict[str, Any]:
        event = await reader.get_event(args.event_id)
        if event is None:
            raise ToolError(f"No event with id '{args.event_id}'.", code="not_found")
        return {"timezone": str(tz), "event": _render(event, tz, detail=True)}

    def evidence_search(raw: dict[str, Any]) -> list[Evidence]:
        return [Evidence(kind="calendar_event", id=e["id"]) for e in raw["events"]]

    def evidence_get(raw: dict[str, Any]) -> list[Evidence]:
        return [Evidence(kind="calendar_event", id=raw["event"]["id"])]

    return (
        ToolSpec(
            name="calendar.search_events",
            version="1",
            summary=(
                "List calendar events that overlap a date range, optionally filtered by text. "
                "Results include weekday, zone, and offset; do not compute times yourself."
            ),
            input_model=SearchEventsInput,
            handler=search,
            untrusted_output=True,
            evidence=evidence_search,
        ),
        ToolSpec(
            name="calendar.get_event",
            version="1",
            summary="Get one calendar event by id, including its description.",
            input_model=GetEventInput,
            handler=get,
            untrusted_output=True,
            evidence=evidence_get,
        ),
    )


# ---- writes ----------------------------------------------------------------------------------
Email = Annotated[str, StringConstraints(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")]
_NOTIFY_HELP = "Send invitations or updates to attendees. Default true."


class CreateEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    start: Annotated[str, StringConstraints(max_length=40)] = Field(description=_WHEN_HELP)
    end: Annotated[str, StringConstraints(max_length=40)] = Field(
        description="End of the event. Two bare dates make an all-day event. " + _WHEN_HELP
    )
    location: Annotated[str, StringConstraints(max_length=300)] | None = None
    attendees: list[Email] = Field(default_factory=list, max_length=50)
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    notify_attendees: bool = Field(default=True, description=_NOTIFY_HELP)

    _fmt = field_validator("start", "end")(_check_format)


class UpdateEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    start: Annotated[str, StringConstraints(max_length=40)] | None = Field(
        default=None, description="New start. " + _WHEN_HELP
    )
    end: Annotated[str, StringConstraints(max_length=40)] | None = Field(
        default=None, description="New end. " + _WHEN_HELP
    )
    location: Annotated[str, StringConstraints(max_length=300)] | None = None
    attendees: list[Email] | None = Field(
        default=None, max_length=50, description="The full attendee list. Replaces the old one."
    )
    description: Annotated[str, StringConstraints(max_length=5000)] | None = None
    notify_attendees: bool = Field(default=True, description=_NOTIFY_HELP)

    @field_validator("start", "end")
    @classmethod
    def _fmt(cls, value: str | None) -> str | None:
        return None if value is None else _check_format(value)

    @model_validator(mode="after")
    def _something_changes(self) -> UpdateEventInput:
        changes = (
            self.title,
            self.start,
            self.end,
            self.location,
            self.attendees,
            self.description,
        )
        if all(c is None for c in changes):
            raise ValueError("give at least one field to change")
        return self


class DeleteEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    notify_attendees: bool = Field(default=True, description=_NOTIFY_HELP)


def _classify_create(args: CreateEventInput) -> RiskClass:
    """Your own calendar is R2. An invitation reaches other people, so it is R3."""
    return RiskClass.R3 if args.attendees else RiskClass.R2


def _always_r3(_args: Any) -> RiskClass:
    """Changing or deleting an existing event may touch other people's calendars."""
    return RiskClass.R3


def _label(event: CalendarEvent, tz: ZoneInfo) -> str:
    """What the user sees for the target: it joins the approved, hashed payload."""
    start, end = event.start.astimezone(tz), event.end.astimezone(tz)
    who = f"; with {', '.join(event.attendees[:5])}" if event.attendees else ""
    return f"{event.title} ({start:%a %Y-%m-%d %H:%M}-{end:%H:%M} {tz}){who}"[:300]


def calendar_write_specs(
    reader: CalendarReader, writer: CalendarWriter, tz: ZoneInfo
) -> tuple[ToolSpec, ToolSpec, ToolSpec]:
    """Create, update, delete. `bind_state` ties an approval to the event as the user saw it."""

    async def bind_existing(args: UpdateEventInput | DeleteEventInput) -> BoundState:
        event = await reader.get_event(args.event_id)
        if event is None:
            raise ToolError(f"No event with id '{args.event_id}'.", code="not_found")
        return BoundState(
            resolved_targets={event.id: _label(event, tz)},
            base_state_hashes={event.id: fingerprint(event)},
        )

    async def create(args: CreateEventInput, ctx: ToolContext) -> dict[str, Any]:
        start = parse_when(args.start, tz, is_end=False)
        end = parse_when(args.end, tz, is_end=True)
        if end <= start:
            raise ToolError("`end` must be after `start`.", code="invalid_arguments")
        # One key per call: a repeat of this exact call can never make a second event.
        key = hashlib.sha256(f"{ctx.run_id}:{ctx.tool_call_id}".encode()).hexdigest()[:32]
        event = await writer.find_created(key)
        if event is None:
            event = await writer.create_event(
                EventDraft(
                    title=args.title,
                    start=start,
                    end=end,
                    timezone=str(tz),
                    all_day=len(args.start) == 10 and len(args.end) == 10,
                    location=args.location,
                    attendees=tuple(args.attendees),
                    description=args.description,
                ),
                request_key=key,
                notify=args.notify_attendees,
            )
        return {
            "timezone": str(tz),
            "event": _render(event, tz, detail=False),
            "invitations_sent": bool(event.attendees and args.notify_attendees),
        }

    async def update(args: UpdateEventInput, ctx: ToolContext) -> dict[str, Any]:
        current = await reader.get_event(args.event_id)
        if current is None:
            raise ToolError(f"No event with id '{args.event_id}'.", code="not_found")
        patch = EventPatch(
            title=args.title,
            start=parse_when(args.start, tz, is_end=False) if args.start else None,
            end=parse_when(args.end, tz, is_end=True) if args.end else None,
            location=args.location,
            attendees=tuple(args.attendees) if args.attendees is not None else None,
            description=args.description,
        )
        if (patch.end or current.end) <= (patch.start or current.start):
            raise ToolError("The event would end before it starts.", code="invalid_arguments")
        event = await writer.update_event(args.event_id, patch, notify=args.notify_attendees)
        if event is None:
            raise ToolError(f"No event with id '{args.event_id}'.", code="not_found")
        return {
            "timezone": str(tz),
            "event": _render(event, tz, detail=False),
            "changed": sorted(n for n in EventPatch.model_fields if getattr(patch, n) is not None),
            "attendees_notified": bool(event.attendees and args.notify_attendees),
        }

    async def delete(args: DeleteEventInput, ctx: ToolContext) -> dict[str, Any]:
        if not await writer.delete_event(args.event_id, notify=args.notify_attendees):
            raise ToolError(f"No event with id '{args.event_id}'.", code="not_found")
        return {"deleted": args.event_id}

    def evidence_event(raw: dict[str, Any]) -> list[Evidence]:
        return [Evidence(kind="calendar_event", id=raw["event"]["id"])]

    def evidence_deleted(raw: dict[str, Any]) -> list[Evidence]:
        return [Evidence(kind="calendar_event_deleted", id=raw["deleted"])]

    note = (
        " The user must approve it first; until the result says it ran, nothing has changed. "
        "Never say it is done without that result."
    )
    return (
        ToolSpec(
            name="calendar.create_event",
            version="1",
            summary="Create a calendar event. With attendees it sends invitations." + note,
            input_model=CreateEventInput,
            handler=create,
            classify=_classify_create,
            idempotency=ToolIdempotency.IDEMPOTENT,
            evidence=evidence_event,
        ),
        ToolSpec(
            name="calendar.update_event",
            version="1",
            summary=(
                "Change an existing event's title, time, place, attendees, or description. "
                "Only the fields you give change." + note
            ),
            input_model=UpdateEventInput,
            handler=update,
            classify=_always_r3,
            idempotency=ToolIdempotency.IDEMPOTENT,
            bind_state=bind_existing,
            evidence=evidence_event,
        ),
        ToolSpec(
            name="calendar.delete_event",
            version="1",
            summary="Delete a calendar event." + note,
            input_model=DeleteEventInput,
            handler=delete,
            classify=_always_r3,
            idempotency=ToolIdempotency.IDEMPOTENT,
            bind_state=bind_existing,
            evidence=evidence_deleted,
        ),
    )
