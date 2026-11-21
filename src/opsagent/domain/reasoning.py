"""Provider-neutral reasoning effort (ARCHITECTURE section 3.5)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum


class ReasoningEffort(StrEnum):
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)


_ORDER: tuple[ReasoningEffort, ...] = tuple(ReasoningEffort)


@dataclass(frozen=True, slots=True)
class ResolvedEffort:
    effort: ReasoningEffort
    adjusted: bool  # True when the requested level was not supported and we fell back


def resolve_effort(
    requested: ReasoningEffort, supported: Collection[ReasoningEffort]
) -> ResolvedEffort | None:
    """Pick the level to send to a model.

    Exact match wins. Otherwise use the nearest *lower* supported level; if the model
    supports nothing that low, use its lowest level. `None` means the model has no
    configurable reasoning.
    """
    if not supported:
        return None
    if requested in supported:
        return ResolvedEffort(requested, adjusted=False)
    lower = [e for e in supported if e.rank < requested.rank]
    pick = max(lower, key=lambda e: e.rank) if lower else min(supported, key=lambda e: e.rank)
    return ResolvedEffort(pick, adjusted=True)
