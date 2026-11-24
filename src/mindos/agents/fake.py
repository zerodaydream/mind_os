"""Scripted fake runtime: replays JSONL scenarios as application events.

It exists to build and test the UI without a real agent (docs/phases/03-tui.md). Each scenario
line is `{"delay": seconds, "event": {...}}`. A `{"suspend": true}` line pauses the run, as an
approval request does; later lines may carry `"when": "approve" | "edit" | "reject"` to branch on
the user's decision. Events get the real `run_id` at replay time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from importlib import resources
from typing import Any, cast

from mindos.domain import AppEvent, RunSpec, events, parse_event
from mindos.domain.commands import Decide

_KEYWORDS = ("approval", "error", "tools", "long")  # anything else plays "stream"
_ID_FIELDS = ("message_id", "tool_call_id", "subagent_id")
_OUTCOME = {"approve": "approved", "edit": "edited", "reject": "rejected"}

Entry = Mapping[str, Any]


def load_scenarios() -> dict[str, list[Entry]]:
    """Read every bundled `scenarios/*.jsonl` file."""
    out: dict[str, list[Entry]] = {}
    for item in resources.files("mindos.agents").joinpath("scenarios").iterdir():
        if item.name.endswith(".jsonl"):
            lines = item.read_text(encoding="utf-8").splitlines()
            out[item.name.removesuffix(".jsonl")] = [json.loads(x) for x in lines if x.strip()]
    return out


class FakeRuntime:
    def __init__(
        self, *, speed: float = 1.0, scenarios: Mapping[str, list[Entry]] | None = None
    ) -> None:
        self._speed = speed
        self._scenarios = dict(scenarios) if scenarios is not None else load_scenarios()
        self._suspended: dict[str, tuple[str, list[Entry], int]] = {}  # thread -> (run, script, i)

    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]:
        return self._play(run.run_id, run.thread_id, self._pick(run.user_text), 0, None)

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]:
        run_id, script, index = self._suspended.pop(thread_id)
        return self._play(run_id, thread_id, script, index, decision)

    async def cancel(self, run_id: str) -> None:
        for thread_id, (suspended_run, _, _) in list(self._suspended.items()):
            if suspended_run == run_id:
                del self._suspended[thread_id]

    def _pick(self, text: str) -> list[Entry]:
        lowered = text.lower()
        name = next((k for k in _KEYWORDS if k in lowered), "stream")
        return self._scenarios[name]

    async def _play(
        self,
        run_id: str,
        thread_id: str,
        script: list[Entry],
        start: int,
        decision: Decide | None,
    ) -> AsyncIterator[AppEvent]:
        if decision is not None:
            yield events.ApprovalResolved(
                run_id=run_id,
                approval_id=decision.approval_id,
                outcome=_OUTCOME[decision.decision],
            )
        for index in range(start, len(script)):
            entry = script[index]
            if entry.get("suspend"):
                self._suspended[thread_id] = (run_id, script, index + 1)
                return
            when = entry.get("when")
            if when is not None and (decision is None or decision.decision != when):
                continue
            await asyncio.sleep(float(entry.get("delay", 0)) / self._speed)
            payload = {**entry["event"], "run_id": run_id}
            for key in _ID_FIELDS:  # scenarios reuse IDs; make them unique per run
                if key in payload:
                    payload[key] = f"{run_id}:{payload[key]}"
            yield cast(AppEvent, parse_event(payload))
