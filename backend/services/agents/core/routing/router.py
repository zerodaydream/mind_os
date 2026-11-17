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


def looks_like_thread_continuation(message: str) -> bool:
    """True for short confirmations (yes, ok, …)."""
    lower = (message or "").strip().lower()
    if not lower:
        return False
    if any(w in lower for w in ("remember", "my name is", "call me", "forget that", "who am i")):
        return False
    if lower in {"yes", "y", "ok", "okay", "sure", "done", "go ahead", "do it", "please do"}:
        return True
    return len(lower) <= 32


def thread_pending_execute(history: list[dict] | None) -> bool:
    """True if recent turns were plan or execute."""
    if not history:
        return False
    for entry in history[-10:]:
        stored = entry.get("intent")
        if stored in ("execute", "plan"):
            return True
        if entry.get("role") != "user":
            continue
        text = (entry.get("content") or "").strip()
        if len(text) < 8:
            continue
        if any(w in text.lower() for w in ("calendar", "schedule", "meeting", "gmail", "file", "/do")):
            return True
    return False