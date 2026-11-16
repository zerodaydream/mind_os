"""Classify chat turns: converse | plan | execute."""

from __future__ import annotations

import re
from typing import Literal

Intent = Literal["converse", "plan", "execute"]

_HTTP_URL = re.compile(r"https?://\S+", re.I)


def url_from_thread(message: str, history: list[dict] | None) -> str | None:
    """First http(s) URL in message or recent history."""
    m = _HTTP_URL.search(message or "")
    if m:
        return m.group(0).rstrip(").,]")
    if not history:
        return None
    thread = "\n".join((entry.get("content") or "") for entry in history[-10:])
    m = _HTTP_URL.search(thread)
    if not m:
        return None
    return m.group(0).rstrip(").,]")


def classify(message: str) -> tuple[Intent, str]:
    """Keyword-free intent stub: currently always plan with full body."""
    body = message.strip()
    return "plan", body