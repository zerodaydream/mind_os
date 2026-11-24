"""Calendar namespace, read-only (Phase 04). Writes arrive in Phase 07 behind approvals.

Time is handled here, not by the model: tools take ISO 8601 text, resolve it in the user's zone,
and render every instant with its zone, offset, and weekday (ARCHITECTURE section 12.4).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from opsagent.domain import CalendarEvent, CalendarReader, Evidence
from opsagent.tools.spec import ToolContext, ToolError, ToolSpec

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
