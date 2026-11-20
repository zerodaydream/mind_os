"""Structured Tier 1 answer shape."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroundedAnswer:
    """Cited answer from a single grounded LLM call."""

    answer_markdown: str
    citations: list[str] = field(default_factory=list)
    confidence: float = 0.5
    needs_more: bool = False
