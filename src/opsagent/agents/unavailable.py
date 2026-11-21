"""Placeholder runtime until the real one lands in Phase 3 (docs/phases/04-agent-core.md)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from opsagent.domain import AppEvent, RunSpec
from opsagent.domain.commands import Decide


class UnavailableRuntime:
    """Fails every run with a clear message instead of pretending to work."""

    _MESSAGE = "The agent runtime is not built yet (arrives in Phase 3)."

    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        raise NotImplementedError(self._MESSAGE)

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        raise NotImplementedError(self._MESSAGE)

    async def cancel(self, run_id: str) -> None:
        return None
