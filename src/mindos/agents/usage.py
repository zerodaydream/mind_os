"""Usage accounting: persist each model call and tell the UI the running session total."""

from __future__ import annotations

import logging

from opsagent.agents.hub import RunState
from opsagent.domain import (
    ModelRole,
    Pricing,
    ReasoningEffort,
    Usage,
    UsageSink,
    estimate_cost,
    events,
)

log = logging.getLogger(__name__)


class UsageTracker:
    """Totals live for the process, which is one UI session."""

    def __init__(self, *, sink: UsageSink, pricing: Pricing | None = None) -> None:
        self._sink = sink
        self._pricing = pricing
        self._input = self._output = self._cached = self._reasoning = 0
        self._cost: float | None = 0.0 if pricing else None

    async def record(
        self,
        state: RunState,
        *,
        role: ModelRole,
        model: str,
        usage: Usage,
        effort: ReasoningEffort | None,
    ) -> None:
        if not (usage.input_tokens or usage.output_tokens):
            return
        cost = estimate_cost(usage, self._pricing)
        try:
            await self._sink.record(
                role=role.value, model=model, usage=usage, latency_ms=None, cost_usd=cost
            )
        except Exception:
            log.exception("usage write failed")  # accounting must not break the run
        self._input += usage.input_tokens
        self._output += usage.output_tokens
        self._cached += usage.cached_tokens
        self._reasoning += usage.reasoning_tokens
        if self._cost is not None and cost is not None:
            self._cost += cost
        state.emit(
            events.UsageUpdated(
                run_id=state.run_id,
                scope="session",
                input_tokens=self._input,
                output_tokens=self._output,
                cached_tokens=self._cached,
                reasoning_tokens=self._reasoning,
                cost_usd=self._cost,
                reasoning_effort=effort,
            )
        )
