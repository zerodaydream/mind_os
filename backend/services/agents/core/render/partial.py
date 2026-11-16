"""Optional phase=think on partial stream callbacks (TUI persists thinking block)."""

from __future__ import annotations

from collections.abc import Callable

PartialFn = Callable[..., None]


def emit_partial(cb: PartialFn | None, text: str, *, phase: str = "answer") -> None:
    """Call streaming callback with optional think vs answer phase."""
    if cb is None:
        return
    try:
        cb(text, phase=phase)
    except TypeError:
        cb(text)