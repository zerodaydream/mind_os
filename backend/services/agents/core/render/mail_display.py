"""Human-readable mail timestamps for chat context and tables."""

from __future__ import annotations

from datetime import datetime, timezone


def format_mail_display_when(raw: str | None) -> str:
    if not raw or not str(raw).strip():
        return "?"
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text[:10] if len(text) >= 10 else text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    h = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b %Y')}, {h}:{local.minute:02d} {ampm}"