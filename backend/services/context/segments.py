"""Typed prompt segments ordered for provider prefix caching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SegmentKind = Literal[
    "core_system",
    "domain",
    "user_profile",
    "session_state",
    "evidence",
    "recent_turns",
    "task",
]


# Most stable → most volatile (KV-cache friendly).
SEGMENT_ORDER: tuple[SegmentKind, ...] = (
    "core_system",
    "domain",
    "user_profile",
    "session_state",
    "evidence",
    "recent_turns",
    "task",
)

# Starting token budgets (tune against eval). Task is never evicted.
DEFAULT_BUDGETS: dict[SegmentKind, int] = {
    "core_system": 600,
    "domain": 250,
    "user_profile": 800,
    "session_state": 500,
    "evidence": 3500,
    "recent_turns": 800,
    "task": 400,
}

# Eviction order when over total budget (never includes task).
EVICT_ORDER: tuple[SegmentKind, ...] = (
    "recent_turns",
    "evidence",
    "session_state",
    "user_profile",
    "domain",
)


@dataclass
class PromptSegment:
    """One named block of prompt text with a soft token budget."""

    kind: SegmentKind
    text: str
    budget_tokens: int | None = None

    def estimate_tokens(self) -> int:
        return max(1, (len(self.text) + 3) // 4) if self.text else 0
