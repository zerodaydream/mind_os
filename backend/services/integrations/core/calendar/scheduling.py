"""Calendar create guards — parse user intent and block invented times."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_OAUTH_ACK = frozenset({"yes", "y", "ok", "okay", "sure", "done", "connected", "ready"})

_HOUR_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_WALL_TIME = re.compile(
    r"\b(\d{1,2}(:\d{2})?\s*(am|pm)|\d{1,2}:\d{2})\b",
    re.I,
)

_CALENDAR_HINTS = (
    "calendar",
    "event",
    "meeting",
    "schedule",
    "reminder",
    "appointment",
    "online meet",
    "free busy",
    "free-busy",
)


def _normalize_time_phrases(text: str) -> str:
    t = text.lower()
    t = re.sub(r"\b([ap])\s*m\.?\b", r"\1m", t)
    t = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+p\s*m\b", r"\1 pm", t)
    t = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+a\s*m\b", r"\1 am", t)
    return t


def user_message_has_wall_time(message: str) -> bool:
    norm = _normalize_time_phrases(message or "")
    if _WALL_TIME.search(norm):
        return True
    for w in _HOUR_WORDS:
        if re.search(rf"\b{w}\s+(am|pm)\b", norm):
            return True
    return any(
        w in norm
        for w in ("noon", "midnight", "morning", "afternoon", "evening", "night")
    )


def _tomorrow_keyword(message: str) -> bool:
    low = (message or "").lower().replace("-", " ")
    return "tomorrow" in low or "to morrow" in low


def mentions_tomorrow(message: str) -> bool:
    return _tomorrow_keyword(message)


def expected_tomorrow_date(tz: str) -> str:
    zone = tz.strip() or "UTC"
    now = datetime.now(ZoneInfo(zone))
    return (now + timedelta(days=1)).strftime("%Y-%m-%d")


def line_looks_calendar_related(text: str) -> bool:
    lower = (text or "").lower()
    if user_message_has_wall_time(text):
        return True
    if _tomorrow_keyword(text) or re.search(r"\btoday\b", lower):
        return True
    if re.search(r"\b(meet|appointment)\b", lower):
        return True
    return any(h in lower for h in _CALENDAR_HINTS)


def collect_calendar_thread(history: list[dict] | None, current: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for entry in (history or [])[-20:]:
        if entry.get("role") != "user":
            continue
        body = (entry.get("content") or "").strip()
        if not body or body.lower() in _OAUTH_ACK:
            continue
        if body in seen:
            continue
        if line_looks_calendar_related(body):
            parts.append(body)
            seen.add(body)
    cur = (current or "").strip()
    if cur and cur.lower() not in _OAUTH_ACK and cur not in seen:
        if line_looks_calendar_related(cur) or parts:
            parts.append(cur)
    return "\n".join(parts) if parts else cur


def assistant_asked_calendar_time(history: list[dict] | None) -> bool:
    if not history:
        return False
    for entry in reversed(history[-10:]):
        if entry.get("role") != "assistant":
            continue
        t = (entry.get("content") or "").lower()
        if any(
            p in t
            for p in (
                "what time",
                "specify a time",
                "time tomorrow",
                "time would you",
                "let me know what time",
                "did not specify a time",
                "no specific time",
                "ask first",
            )
        ):
            return True
    return False


def pending_calendar_slot_reply(message: str, history: list[dict] | None) -> bool:
    if not assistant_asked_calendar_time(history):
        return False
    body = (message or "").strip()
    if not body or body.lower() in _OAUTH_ACK:
        return False
    return user_message_has_wall_time(body) or len(body) < 100


def parse_wall_clock(message: str) -> tuple[int, int] | None:
    norm = _normalize_time_phrases(message or "")
    if re.search(r"\bnoon\b", norm):
        return 12, 0
    if re.search(r"\bmidnight\b", norm):
        return 0, 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", norm)
    if m:
        h = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            if h != 12:
                h += 12
        elif h == 12:
            h = 0
        return h, minute
    wm = re.search(
        r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
        r"(?:\s*(?::(\d{2}))?)?\s*(am|pm)\b",
        norm,
    )
    if wm:
        h = _HOUR_WORDS[wm.group(1)]
        minute = int(wm.group(2) or 0)
        if wm.group(3).startswith("p"):
            if h != 12:
                h += 12
        elif h == 12:
            h = 0
        return h, minute
    return None


def _event_date_from_text(message: str, tz: str) -> date | None:
    try:
        zone = ZoneInfo((tz or "UTC").strip() or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    now = datetime.now(zone)
    low = (message or "").lower()
    if _tomorrow_keyword(message):
        return (now + timedelta(days=1)).date()
    if re.search(r"\btoday\b", low):
        return now.date()
    return None


def resolve_event_start(task_message: str, tz: str) -> str | None:
    """Derive naive local start_iso from user text (thread); None if incomplete."""
    clock = parse_wall_clock(task_message)
    if clock is None:
        return None
    day = _event_date_from_text(task_message, tz)
    if day is None:
        return None
    hour, minute = clock
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00"


def _start_iso_utc_instant(start_iso: str) -> bool:
    s = (start_iso or "").strip()
    if not s:
        return False
    if s.upper().endswith("Z"):
        return True
    if "+00:00" in s.upper():
        return True
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.tzinfo is not None and dt.utcoffset() == timedelta(0)
    except ValueError:
        return False


def validate_create_schedule(
    *,
    task_message: str,
    start_iso: str,
    all_day: bool,
    tz: str,
) -> str | None:
    if all_day:
        return None if (start_iso or "").strip() else "Need a date for an all-day event."
    if not user_message_has_wall_time(task_message):
        return (
            "User did not specify a time. Ask what time (e.g. 1 PM), "
            "then call create again — do not guess."
        )
    if _tomorrow_keyword(task_message):
        exp = expected_tomorrow_date(tz)
        got = (start_iso or "").strip()[:10]
        if len(got) == 10 and got != exp:
            return (
                f"start_iso date {got} is not tomorrow ({exp} in {tz}). "
                "Use the Clock tomorrow date, then retry."
            )
    zone = (tz or "UTC").strip() or "UTC"
    if user_message_has_wall_time(task_message) and zone.upper() != "UTC":
        if _start_iso_utc_instant(start_iso):
            return (
                f"start_iso must be naive local wall time in {zone} "
                "(YYYY-MM-DDTHH:MM:SS, no Z or +00:00)."
            )
    return None