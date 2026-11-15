"""Google Calendar API (OAuth token shared with Gmail integration)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import IntegrationAccount
from backend.services.integrations.core.gmail_sync import ensure_google_access_token

DEFAULT_DURATION_MIN = 30
_API = "https://www.googleapis.com/calendar/v3"
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def missing_calendar_fields(title: str, start_iso: str) -> list[str]:
    missing: list[str] = []
    if not (title or "").strip():
        missing.append("title")
    if not (start_iso or "").strip():
        missing.append("start datetime (start_iso)")
    return missing


def _parse_iso_datetime(iso: str, time_zone: str) -> datetime:
    raw = iso.strip()
    if not raw:
        raise ValueError("empty datetime")
    text = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid start_iso/end_iso: {raw}") from exc
    if dt.tzinfo is None:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(time_zone))
        except Exception as exc:
            raise ValueError(f"invalid time_zone: {time_zone}") from exc
    return dt


def _split_emails(raw: str | Sequence[str]) -> list[str]:
    if isinstance(raw, str):
        return [e.strip() for e in re.split(r"[,;\s]+", raw) if "@" in e]
    return [str(e).strip() for e in raw if str(e).strip() and "@" in str(e)]


def _rrules(raw: str | Sequence[str]) -> list[str]:
    if not raw:
        return []
    items = list(raw) if not isinstance(raw, str) else [x.strip() for x in re.split(r"[\n,]+", raw) if x.strip()]
    out: list[str] = []
    for r in items:
        r = r.strip()
        if not r:
            continue
        out.append(r if r.upper().startswith("RRULE:") else f"RRULE:{r}")
    return out


def build_event_window(
    start_iso: str,
    end_iso: str,
    time_zone: str,
    *,
    default_duration_min: int = DEFAULT_DURATION_MIN,
) -> tuple[dict, dict]:
    """Return Google Calendar start/end dicts (dateTime + timeZone)."""
    tz = time_zone.strip() or "UTC"
    start = _parse_iso_datetime(start_iso, tz)
    if (end_iso or "").strip():
        end = _parse_iso_datetime(end_iso, tz)
    else:
        end = start + timedelta(minutes=max(1, int(default_duration_min)))
    if end <= start:
        raise ValueError("end must be after start")

    def _slot(dt: datetime) -> dict:
        local = dt.astimezone(ZoneInfo(tz))
        return {"dateTime": local.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": tz}

    return _slot(start), _slot(end)


def build_event_body(
    *,
    title: str,
    start_iso: str,
    end_iso: str = "",
    description: str = "",
    location: str = "",
    time_zone: str = "UTC",
    all_day: bool = False,
    recurrence: str | Sequence[str] = "",
    attendees: str | Sequence[str] = "",
    reminders_minutes: int | None = None,
) -> dict:
    missing = missing_calendar_fields(title, start_iso)
    if missing:
        raise ValueError("missing: " + ", ".join(missing))

    use_all_day = all_day or bool(_DATE_ONLY.match(start_iso.strip()))
    body: dict = {"summary": title.strip()}

    if use_all_day:
        start_d = start_iso.strip()[:10]
        if (end_iso or "").strip():
            end_d = end_iso.strip()[:10]
        else:
            end_d = (date.fromisoformat(start_d) + timedelta(days=1)).isoformat()
        if date.fromisoformat(end_d) <= date.fromisoformat(start_d):
            raise ValueError("all-day end date must be after start date")
        body["start"] = {"date": start_d}
        body["end"] = {"date": end_d}
    else:
        start, end = build_event_window(start_iso, end_iso, time_zone)
        body["start"] = start
        body["end"] = end

    if (description or "").strip():
        body["description"] = description.strip()
    if (location or "").strip():
        body["location"] = location.strip()

    rules = _rrules(recurrence)
    if rules:
        body["recurrence"] = rules

    emails = _split_emails(attendees)
    if emails:
        body["attendees"] = [{"email": e} for e in emails]

    if reminders_minutes is not None and int(reminders_minutes) >= 0:
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": int(reminders_minutes)}],
        }

    return body


async def google_calendar_connected(session: AsyncSession, user) -> bool:
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
            IntegrationAccount.status == "active",
        )
    )
    return result.scalar_one_or_none() is not None


async def _google_calendar_token(session: AsyncSession, user, settings: Settings) -> str:
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
            IntegrationAccount.status == "active",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("Google not connected; GET /v1/integrations/google/start")
    return await ensure_google_access_token(session, account, settings)


async def _calendar_request(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    method: str,
    path: str,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict | list:
    token = await _google_calendar_token(session, user, settings)
    url = f"{_API}{path}"

    async def _once(bearer: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.request(
                method,
                url,
                headers={"Authorization": f"Bearer {bearer}"},
                json=json_body,
                params=params,
            )

    resp = await _once(token)
    if resp.status_code == 401:
        result = await session.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == user.id,
                IntegrationAccount.provider == "google",
                IntegrationAccount.status == "active",
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise ValueError("Google not connected; GET /v1/integrations/google/start")
        token = await ensure_google_access_token(session, account, settings, force_refresh=True)
        resp = await _once(token)

    if resp.status_code >= 400:
        detail = resp.text.strip()[:500]
        if resp.status_code == 403 and "insufficient" in detail.lower():
            raise ValueError(
                "Calendar permission missing; reconnect Google at "
                "/v1/integrations/google/start (Calendar scope added)."
            )
        raise ValueError(f"Calendar API error {resp.status_code}: {detail}")
    if not resp.content:
        return {}
    return resp.json()


def format_when_label(raw: str) -> str:
    """Human-readable start for list/create replies (not raw API ISO)."""
    if not raw or raw == "?":
        return raw or "?"
    s = raw.strip()
    if _DATE_ONLY.match(s):
        try:
            return date.fromisoformat(s).strftime("%a %d %b %Y")
        except ValueError:
            return s
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        hour = dt.strftime("%I").lstrip("0") or "12"
        return f"{dt.strftime('%a %d %b %Y')}, {hour}:{dt.strftime('%M %p')}"
    except ValueError:
        return s


def _event_start_label(ev: dict) -> str:
    start = ev.get("start") or {}
    raw = start.get("dateTime") or start.get("date") or "?"
    return format_when_label(raw) if raw != "?" else "?"


def format_event_lines(events: list[dict], *, limit: int = 20) -> str:
    if not events:
        return "No matching calendar events."
    lines: list[str] = []
    for ev in events[: max(1, limit)]:
        eid = ev.get("id") or ""
        summ = ev.get("summary") or "(no title)"
        when = _event_start_label(ev)
        link = ev.get("htmlLink") or ""
        tail = f" Link: {link}" if link else ""
        lines.append(f"- {summ} @ {when} (id={eid}){tail}")
    return "\n".join(lines)


async def create_calendar_event(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    title: str,
    start_iso: str,
    end_iso: str = "",
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    time_zone: str = "UTC",
    all_day: bool = False,
    recurrence: str | Sequence[str] = "",
    attendees: str | Sequence[str] = "",
    reminders_minutes: int | None = None,
) -> dict:
    body = build_event_body(
        title=title,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
        time_zone=time_zone,
        all_day=all_day,
        recurrence=recurrence,
        attendees=attendees,
        reminders_minutes=reminders_minutes,
    )
    cal = (calendar_id or "primary").strip() or "primary"
    send = "all" if body.get("attendees") else "none"
    # shortcut: calendar_id from agent only; encode if exposing to untrusted clients
    return await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="POST",
        path=f"/calendars/{cal}/events",
        json_body=body,
        params={"sendUpdates": send},
    )


async def list_calendar_events(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    calendar_id: str = "primary",
    time_min_iso: str = "",
    time_max_iso: str = "",
    query: str = "",
    max_results: int = 15,
) -> list[dict]:
    cal = (calendar_id or "primary").strip() or "primary"
    now = datetime.now(timezone.utc)
    time_min = time_min_iso.strip() or now.isoformat().replace("+00:00", "Z")
    params: dict = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max(1, min(int(max_results), 50))),
        "timeMin": time_min,
    }
    if time_max_iso.strip():
        params["timeMax"] = time_max_iso.strip()
    if query.strip():
        params["q"] = query.strip()
    data = await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="GET",
        path=f"/calendars/{cal}/events",
        params=params,
    )
    return list(data.get("items") or []) if isinstance(data, dict) else []


async def update_calendar_event(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    event_id: str,
    calendar_id: str = "primary",
    title: str = "",
    start_iso: str = "",
    end_iso: str = "",
    description: str = "",
    location: str = "",
    time_zone: str = "UTC",
    all_day: bool = False,
    recurrence: str | Sequence[str] = "",
    attendees: str | Sequence[str] = "",
    reminders_minutes: int | None = None,
) -> dict:
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("missing event_id")
    cal = (calendar_id or "primary").strip() or "primary"
    patch: dict = {}
    if title.strip():
        patch["summary"] = title.strip()
    if start_iso.strip():
        full = build_event_body(
            title=title.strip() or "event",
            start_iso=start_iso,
            end_iso=end_iso,
            description=description,
            location=location,
            time_zone=time_zone,
            all_day=all_day,
            recurrence=recurrence,
            attendees=attendees,
            reminders_minutes=reminders_minutes,
        )
        patch.update({k: full[k] for k in ("start", "end") if k in full})
    if description.strip():
        patch["description"] = description.strip()
    if location.strip():
        patch["location"] = location.strip()
    rules = _rrules(recurrence)
    if rules:
        patch["recurrence"] = rules
    emails = _split_emails(attendees)
    if emails:
        patch["attendees"] = [{"email": e} for e in emails]
    if reminders_minutes is not None and int(reminders_minutes) >= 0:
        patch["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": int(reminders_minutes)}],
        }
    if not patch:
        raise ValueError("nothing to update — pass at least one field")
    send = "all" if patch.get("attendees") else "none"
    return await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="PATCH",
        path=f"/calendars/{cal}/events/{eid}",
        json_body=patch,
        params={"sendUpdates": send},
    )


def split_event_ids(raw: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;\s]+", (raw or "").strip()) if x.strip()]


async def delete_calendar_events(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    event_ids: str | Sequence[str],
    calendar_id: str = "primary",
) -> int:
    ids = split_event_ids(event_ids) if isinstance(event_ids, str) else [str(x).strip() for x in event_ids if str(x).strip()]
    if not ids:
        raise ValueError("missing event_id")
    for eid in ids:
        await delete_calendar_event(
            session,
            user=user,
            settings=settings,
            event_id=eid,
            calendar_id=calendar_id,
        )
    return len(ids)


async def delete_calendar_event(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    event_id: str,
    calendar_id: str = "primary",
) -> None:
    eid = (event_id or "").strip()
    if not eid:
        raise ValueError("missing event_id")
    cal = (calendar_id or "primary").strip() or "primary"
    await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="DELETE",
        path=f"/calendars/{cal}/events/{eid}",
    )


async def list_calendars(session: AsyncSession, *, user, settings: Settings) -> list[dict]:
    data = await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="GET",
        path="/users/me/calendarList",
        params={"minAccessRole": "writer"},
    )
    return list(data.get("items") or []) if isinstance(data, dict) else []


def format_calendar_list(items: list[dict]) -> str:
    if not items:
        return "No writable calendars."
    lines = []
    for c in items[:30]:
        cid = c.get("id") or ""
        summary = c.get("summary") or cid
        primary = " (primary)" if c.get("primary") else ""
        lines.append(f"- {summary}{primary} id={cid}")
    return "\n".join(lines)


async def query_free_busy(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    time_min_iso: str,
    time_max_iso: str,
    calendar_ids: str | Sequence[str] = "primary",
) -> str:
    t0 = time_min_iso.strip()
    t1 = time_max_iso.strip()
    if not t0 or not t1:
        raise ValueError("time_min_iso and time_max_iso required")
    if isinstance(calendar_ids, str):
        ids = [x.strip() for x in calendar_ids.split(",") if x.strip()] or ["primary"]
    else:
        ids = [str(x).strip() for x in calendar_ids if str(x).strip()]
    body = {
        "timeMin": t0,
        "timeMax": t1,
        "items": [{"id": i} for i in ids],
    }
    data = await _calendar_request(
        session,
        user=user,
        settings=settings,
        method="POST",
        path="/freeBusy",
        json_body=body,
    )
    calendars = (data.get("calendars") or {}) if isinstance(data, dict) else {}
    lines: list[str] = []
    for cid in ids:
        info = calendars.get(cid) or {}
        busy = info.get("busy") or []
        if not busy:
            lines.append(f"{cid}: free")
            continue
        for slot in busy[:12]:
            lines.append(f"{cid}: busy {slot.get('start')} — {slot.get('end')}")
    return "\n".join(lines) if lines else "No busy data returned."