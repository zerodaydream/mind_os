"""Google Calendar event creation (OAuth token shared with Gmail integration)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import IntegrationAccount
from backend.services.integrations.core.gmail_sync import ensure_google_access_token

DEFAULT_DURATION_MIN = 30


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
) -> dict:
    missing = missing_calendar_fields(title, start_iso)
    if missing:
        raise ValueError("missing: " + ", ".join(missing))
    start, end = build_event_window(start_iso, end_iso, time_zone)
    body: dict = {"summary": title.strip(), "start": start, "end": end}
    if (description or "").strip():
        body["description"] = description.strip()
    if (location or "").strip():
        body["location"] = location.strip()
    return body


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
) -> dict:
    body = build_event_body(
        title=title,
        start_iso=start_iso,
        end_iso=end_iso,
        description=description,
        location=location,
        time_zone=time_zone,
    )
    cal = (calendar_id or "primary").strip() or "primary"

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

    token = await ensure_google_access_token(session, account, settings)
    # shortcut: calendar_id from agent only; encode if exposing to untrusted clients
    url = f"https://www.googleapis.com/calendar/v3/calendars/{cal}/events"

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        if resp.status_code == 401:
            token = await ensure_google_access_token(session, account, settings, force_refresh=True)
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
        if resp.status_code >= 400:
            detail = resp.text.strip()[:500]
            if resp.status_code == 403 and "insufficient" in detail.lower():
                raise ValueError(
                    "Calendar permission missing; reconnect Google at "
                    "/v1/integrations/google/start (Calendar scope added)."
                )
            raise ValueError(f"Calendar API error {resp.status_code}: {detail}")
        return resp.json()