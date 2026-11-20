"""Structured turn plan — replaces text-line TurnPrep protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

TurnTier = Literal[0, 1, 2]
TurnScope = Literal["in", "clarify", "sorry"]
TurnIntent = Literal["converse", "plan", "execute"]


@dataclass
class RetrievalFilters:
    """Structured filters for the retrieval plane."""

    sender: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    sources: list[str] = field(default_factory=list)
    has_attachment: bool | None = None
    entities: list[str] = field(default_factory=list)


@dataclass
class TurnPlan:
    """Planner output for one user turn (JSON-schema friendly)."""

    scope: TurnScope = "in"
    intent: TurnIntent = "plan"
    queries: list[str] = field(default_factory=list)
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    entities: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    writes_external: bool = False
    confidence: float = 0.5
    clarify_reply: str = ""
    task: str = ""
    tier: TurnTier | None = None
    skip_retrieval: bool = False

    def resolved_tier(self, *, needs_more: bool = False, insufficient: bool = False) -> TurnTier:
        """Map plan + retrieval signals to Tier 0 / 1 / 2."""
        if self.tier is not None:
            return self.tier
        if self.scope != "in":
            return 0
        if self.confidence < 0.35:
            return 0
        if self.writes_external or self.intent == "execute":
            return 2
        if needs_more or insufficient:
            return 2
        return 1
