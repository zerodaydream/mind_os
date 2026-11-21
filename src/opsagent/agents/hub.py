"""Per-run state shared by the runtime, the tool bridge, and the subagent runner.

One `RunState` exists for the life of a run. It owns the event queue that serializes events from
the model stream and from tools into one ordered stream, and the run's budget and ledgers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from opsagent.agents.limits import RunBudget, RunLimits
from opsagent.domain import AppEvent, Evidence, ToolResult


class _Done:
    """Queue sentinel: the worker has finished."""


DONE = _Done()
QueueItem = AppEvent | _Done | BaseException


@dataclass
class Ledger:
    """What one subagent actually did, recorded by code from engine results, not from the model."""

    evidence: list[Evidence] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, result: ToolResult) -> None:
        if result.status == "ok":
            for item in result.evidence:
                if item not in self.evidence:
                    self.evidence.append(item)
        else:
            self.errors.append(f"{result.tool}: {result.error_code}")
        if result.artifact_ref:
            self.artifacts.append(result.artifact_ref)


class RunState:
    def __init__(self, run_id: str, limits: RunLimits) -> None:
        self.run_id = run_id
        self.limits = limits
        self.budget = RunBudget(limits.max_tool_calls)
        self.slots = asyncio.Semaphore(limits.max_parallel_subagents)
        self.queue: asyncio.Queue[QueueItem] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None
        self._ledgers: dict[str, Ledger] = {}

    def emit(self, event: AppEvent) -> None:
        self.queue.put_nowait(event)

    def ledger(self, subagent_id: str) -> Ledger:
        return self._ledgers.setdefault(subagent_id, Ledger())


class RunHub:
    """Looks up the state of active runs by ID."""

    def __init__(self) -> None:
        self._runs: dict[str, RunState] = {}

    def open(self, run_id: str, limits: RunLimits) -> RunState:
        if run_id in self._runs:
            raise ValueError(f"run {run_id} is already open")
        state = self._runs[run_id] = RunState(run_id, limits)
        return state

    def get(self, run_id: str | None) -> RunState | None:
        return self._runs.get(run_id) if run_id else None

    def close(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
