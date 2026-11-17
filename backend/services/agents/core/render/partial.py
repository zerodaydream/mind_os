"""Optional phase=think on partial stream callbacks (TUI persists thinking block)."""

from __future__ import annotations

import re
from collections.abc import Callable

PartialFn = Callable[..., None]

THINK_PLAN_HEADER = "Planning moves:"

# Model thoughts that leak training/meta phrasing — never show verbatim in UI.
_THINK_META = re.compile(
    r"(?i)\b("
    r"system prompt|system instructions?|cross[- ]referenc|dissecting|"
    r"user(?:'s)? request|before proceeding|available resources|"
    r"my approach|ensure my approach|optimized before|"
    r"against the (?:system|instructions)|analyz(?:e|ing) (?:the )?request|"
    r"internal (?:tool|prompt)"
    r")\b"
)
_THINK_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


def polish_think_for_ui(text: str) -> str:
    """User-visible thinking: concrete progress only, no meta-instruction talk."""
    raw = (text or "").strip()
    if not raw:
        return raw
    if not _THINK_META.search(raw):
        return raw
    kept = [s.strip() for s in _THINK_SENTENCE.split(raw) if s.strip() and not _THINK_META.search(s)]
    if kept:
        return "\n".join(kept)
    return "Working on it…"


def merge_stream_text(prev: str, piece: str) -> str:
    """Merge LLM stream chunks; cumulative snapshots replace prior text."""
    piece = (piece or "").strip()
    if not piece:
        return prev or ""
    prev = prev or ""
    if not prev:
        return piece
    if piece.startswith(prev):
        return piece
    if prev in piece and len(piece) >= len(prev):
        return piece
    if piece in prev:
        return prev
    if prev.endswith(piece):
        return prev
    return f"{prev}{piece}"


def emit_canned_stream(cb: PartialFn | None, text: str, *, phase: str = "answer") -> None:
    """Fixed copy through on_partial so the TUI can typewriter-reveal (see stream_reveal)."""
    t = (text or "").strip()
    if cb is None or not t:
        return
    try:
        cb(t, phase=phase, stream_reveal=True)
    except TypeError:
        emit_partial(cb, t, phase=phase)


def emit_partial(cb: PartialFn | None, text: str, *, phase: str = "answer") -> None:
    """Call streaming callback with optional think vs answer phase."""
    if cb is None:
        return
    if phase == "think" and text.strip().startswith("[tool]"):
        pass  # tool status lines from the harness
    elif phase == "think":
        text = polish_think_for_ui(text)
        if not text:
            return
    try:
        cb(text, phase=phase)
    except TypeError:
        cb(text)