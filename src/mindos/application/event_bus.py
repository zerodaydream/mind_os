"""In-process event bus (ARCHITECTURE section 13).

Publishing never blocks: each subscriber has an unbounded queue, so a slow UI cannot stall the
agent. The bus assigns a per-run `seq` and a timestamp. Sequence state for a run is dropped once
the run's terminal event has been published.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from opsagent.domain import TERMINAL_EVENTS, AppEvent, Clock

_SESSION_KEY = "_session"


class Subscription:
    """Async iterator over events. A run-scoped subscription ends after the run's terminal event."""

    def __init__(self, run_id: str | None) -> None:
        self._run_id = run_id
        self._queue: asyncio.Queue[AppEvent] = asyncio.Queue()
        self._done = False

    def _offer(self, event: AppEvent) -> None:
        if self._run_id is None or event.run_id == self._run_id:
            self._queue.put_nowait(event)

    def __aiter__(self) -> AsyncIterator[AppEvent]:
        return self

    async def __anext__(self) -> AppEvent:
        if self._done:
            raise StopAsyncIteration
        event = await self._queue.get()
        if self._run_id is not None and isinstance(event, TERMINAL_EVENTS):
            self._done = True
        return event

    def pending(self) -> int:
        return self._queue.qsize()


class EventBus:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._seq: dict[str, int] = {}
        self._subscribers: list[Subscription] = []

    def publish(self, event: AppEvent) -> AppEvent:
        """Stamp and fan out an event. Returns the stamped copy."""
        key = event.run_id or _SESSION_KEY
        seq = self._seq.get(key, 0) + 1
        stamped: AppEvent = event.model_copy(update={"seq": seq, "ts": self._clock.now()})
        if isinstance(stamped, TERMINAL_EVENTS):
            self._seq.pop(key, None)
        else:
            self._seq[key] = seq
        for sub in tuple(self._subscribers):
            sub._offer(stamped)
        return stamped

    @contextmanager
    def subscribe(self, run_id: str | None = None) -> Iterator[Subscription]:
        """Subscribe to all events, or only to one run (the stream ends at its terminal event)."""
        sub = Subscription(run_id)
        self._subscribers.append(sub)
        try:
            yield sub
        finally:
            self._subscribers.remove(sub)
