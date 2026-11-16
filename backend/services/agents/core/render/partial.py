"""Optional phase=think on partial stream callbacks (TUI persists thinking block)."""

from __future__ import annotations

from collections.abc import Callable

PartialFn = Callable[..., None]


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


def emit_partial(cb: PartialFn | None, text: str, *, phase: str = "answer") -> None:
    """Call streaming callback with optional think vs answer phase."""
    if cb is None:
        return
    try:
        cb(text, phase=phase)
    except TypeError:
        cb(text)