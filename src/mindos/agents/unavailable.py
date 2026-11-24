"""Stand-in runtime for a container built without a database or checkpointer."""

from __future__ import annotations

from collections.abc import AsyncIterator

from mindos.domain import AppEvent, RunSpec
from mindos.domain.commands import Decide


class UnavailableRuntime:
    """Fails every run with a clear message instead of pretending to work."""

    _MESSAGE = "The agent runtime is not connected. Start the database and run `mindos db migrate`."

    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        raise NotImplementedError(self._MESSAGE)

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        raise NotImplementedError(self._MESSAGE)

    async def cancel(self, run_id: str) -> None:
        return None
