"""Cost estimation from token usage. Prices are configuration, never code."""

from __future__ import annotations

from dataclasses import dataclass

from opsagent.domain.llm import Usage


@dataclass(frozen=True, slots=True)
class Pricing:
    """USD per million tokens. Reasoning tokens are part of `output_tokens`, so they bill once."""

    input_per_mtok: float
    output_per_mtok: float


def estimate_cost(usage: Usage, pricing: Pricing | None) -> float | None:
    """Estimated USD for one usage record, or None when no pricing is configured."""
    if pricing is None:
        return None
    return (
        usage.input_tokens * pricing.input_per_mtok + usage.output_tokens * pricing.output_per_mtok
    ) / 1_000_000
