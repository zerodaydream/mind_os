"""Resolve user IANA timezone for calendar (no hardcoded region)."""

from __future__ import annotations

import os
from datetime import datetime

from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.services.integrations.core.calendar.events import (
    google_calendar_auth_status,
    _calendar_request,
)


# shortcut: abbrev is ambiguous globally; only use when utcoffset matches that zone
_ABBREV_IANA: dict[str, str] = {
    "IST": "Asia/Kolkata",
}


def detect_client_iana_timezone() -> str:
    """Local IANA zone from OS (CLI); complements Google Calendar settings API."""
    now = datetime.now().astimezone()
    tz = now.tzinfo
    if isinstance(tz, ZoneInfo):
        return tz.key
    key = getattr(tz, "key", None)
    if isinstance(key, str) and key:
        return _valid_iana(key) or key
    off = now.utcoffset()
    if off is not None:
        abbrev = (now.tzname() or "").strip().upper()
        candidate = _ABBREV_IANA.get(abbrev)
        if candidate:
            try:
                if datetime.now(ZoneInfo(candidate)).utcoffset() == off:
                    return candidate
            except Exception:
                pass
    env = (os.environ.get("TZ") or "").strip()
    if env:
        v = _valid_iana(env)
        if v:
            return v
    try:
        link = os.readlink("/etc/localtime")
    except OSError:
        link = ""
    if "zoneinfo/" in link:
        name = link.split("zoneinfo/", 1)[1]
        v = _valid_iana(name)
        if v:
            return v
    return ""


def timezone_from_message(message: str) -> str | None:
    lower = (message or "").lower()
    if (
        "asia/kolkata" in lower
        or " ist" in lower
        or lower.endswith("ist")
        or "india" in lower
    ):
        return "Asia/Kolkata"
    return None


def _valid_iana(name: str) -> str | None:
    raw = (name or "").strip()
    if not raw:
        return None
    try:
        ZoneInfo(raw)
        return raw
    except Exception:
        return None


async def google_calendar_timezone(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
) -> str | None:
    if await google_calendar_auth_status(session, user) != "ok":
        return None
    try:
        data = await _calendar_request(
            session,
            user=user,
            settings=settings,
            method="GET",
            path="/users/me/settings/timezone",
        )
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return _valid_iana(str(data.get("value") or ""))


async def resolve_user_timezone(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    message: str = "",
    client_tz: str = "",
) -> str:
    msg_tz = timezone_from_message(message)
    if msg_tz:
        return msg_tz
    cli = _valid_iana(client_tz) or _valid_iana(detect_client_iana_timezone())
    if cli:
        return cli
    if session is not None:
        gcal = await google_calendar_timezone(session, user=user, settings=settings)
        if gcal:
            return gcal
    env = _valid_iana(getattr(settings, "mindos_default_timezone", "") or "")
    if env:
        return env
    return "UTC"


def effective_calendar_tz(explicit: str, default_tz: str) -> str:
    """Map model's habitual time_zone=UTC to the resolved user zone."""
    e = (explicit or "").strip()
    d = (default_tz or "UTC").strip() or "UTC"
    # shortcut: LLM often sends UTC for local wall times
    if e.upper() == "UTC" and d.upper() != "UTC":
        return d
    if e:
        return e
    return d