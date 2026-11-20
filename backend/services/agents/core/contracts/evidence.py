"""Typed retrieval evidence — Phase 1 service returns this; Phase 2 cites eid."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Evidence:
    """One citable retrieval hit."""

    eid: str
    kind: str
    text: str
    score: float = 0.0
    artifact_id: str | None = None
    title: str | None = None
    uri: str | None = None
    as_of: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidencePack:
    """Retrieval output for one turn: scored evidence plus diagnostics."""

    items: list[Evidence] = field(default_factory=list)
    insufficient: bool = False
    index_as_of: datetime | None = None
    queries: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    rerank_margin: float | None = None

    def by_eid(self) -> dict[str, Evidence]:
        return {e.eid: e for e in self.items}

    @property
    def has_evidence(self) -> bool:
        return bool(self.items)
