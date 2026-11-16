"""CLI step lines from outbox handlers (avoids consumer ↔ handler import cycle)."""

from __future__ import annotations

import contextvars
from collections.abc import Callable

_step: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "outbox_step", default=None
)


def outbox_say(message: str) -> None:
    fn = _step.get()
    if fn is not None:
        fn(message)


def bind_outbox_say(fn: Callable[[str], None] | None) -> contextvars.Token | None:
    if fn is None:
        return None
    return _step.set(fn)


def reset_outbox_say(token: contextvars.Token | None) -> None:
    if token is not None:
        _step.reset(token)