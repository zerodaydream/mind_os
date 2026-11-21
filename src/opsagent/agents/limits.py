"""Per-run caps (ARCHITECTURE section 3.3): steps, tool calls, wall-clock deadline, concurrency."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RunLimits:
    max_steps: int = 25  # model/tool rounds for the orchestrator
    subagent_steps: int = 12  # rounds for one subagent invocation
    max_tool_calls: int = 40  # across the orchestrator and every subagent
    deadline_s: float = 900.0
    max_parallel_subagents: int = 3

    def recursion(self, steps: int) -> int:
        """LangGraph counts one model node and one tool node per round, plus the entry."""
        return 2 * steps + 1


class RunBudget:
    """Counts tool calls for one run. Not shared between runs."""

    def __init__(self, max_tool_calls: int) -> None:
        self._max = max_tool_calls
        self._used = 0

    @property
    def used(self) -> int:
        return self._used

    def admit(self) -> str | None:
        """Charge one call. Returns a refusal message once the budget is spent."""
        if self._used >= self._max:
            return (
                f"The tool-call budget of {self._max} is spent. Stop calling tools, "
                "report what is done and what remains."
            )
        self._used += 1
        return None
