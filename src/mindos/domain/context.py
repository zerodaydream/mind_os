"""Correlation IDs carried through logs, audit records, and events (ARCHITECTURE section 15).

Stdlib only. The mapping is immutable; `bind` swaps it for the duration of a block.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType

CORRELATION_KEYS: tuple[str, ...] = (
    "session_id",
    "conversation_id",
    "thread_id",
    "run_id",
    "step_id",
    "subagent_id",
    "tool_call_id",
    "approval_id",
    "job_id",
    "request_id",
)

_EMPTY: Mapping[str, str] = MappingProxyType({})
_CURRENT: ContextVar[Mapping[str, str]] = ContextVar("mindos_correlation", default=_EMPTY)


def current() -> Mapping[str, str]:
    """Correlation IDs bound in the current context."""
    return _CURRENT.get()


@contextmanager
def bind(**ids: str | None) -> Iterator[None]:
    """Add correlation IDs for the duration of the block. `None` values are ignored."""
    unknown = set(ids) - set(CORRELATION_KEYS)
    if unknown:
        raise ValueError(f"unknown correlation keys: {sorted(unknown)}")
    merged = {**_CURRENT.get(), **{k: v for k, v in ids.items() if v is not None}}
    token = _CURRENT.set(MappingProxyType(merged))
    try:
        yield
    finally:
        _CURRENT.reset(token)
