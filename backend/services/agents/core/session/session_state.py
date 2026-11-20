"""Structured per-chat session state (replaces unbounded SESSION.md growth)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ChatSessionState:
    """Rolling structured summary for one chat thread."""

    goal: str = ""
    decisions: list[str] = field(default_factory=list)
    entities_in_focus: list[dict[str, Any]] = field(default_factory=list)
    artifact_ids_in_focus: list[str] = field(default_factory=list)
    open_loops: list[str] = field(default_factory=list)
    pending_confirmations: list[dict[str, Any]] = field(default_factory=list)
    last_evidence_eids: list[str] = field(default_factory=list)

    def to_prompt_block(self, *, max_chars: int = 1_600) -> str:
        """Compact text for ContextCompiler session_state segment."""
        lines: list[str] = ["Session state:"]
        if self.goal:
            lines.append(f"Goal: {self.goal}")
        if self.decisions:
            lines.append("Decisions: " + "; ".join(self.decisions[-5:]))
        if self.entities_in_focus:
            labels = [
                e.get("name") or e.get("canonical_name") or str(e)
                for e in self.entities_in_focus[-8:]
            ]
            lines.append("Entities: " + ", ".join(str(x) for x in labels if x))
        if self.artifact_ids_in_focus:
            lines.append(
                "Artifacts: " + ", ".join(self.artifact_ids_in_focus[-8:])
            )
        if self.open_loops:
            lines.append("Open: " + "; ".join(self.open_loops[-5:]))
        if self.pending_confirmations:
            lines.append(f"Pending confirmations: {len(self.pending_confirmations)}")
        if self.last_evidence_eids:
            lines.append("Last evidence: " + ", ".join(self.last_evidence_eids[:12]))
        text = "\n".join(lines)
        if len(text) > max_chars:
            return text[: max_chars - 1].rstrip() + "…"
        return text

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> ChatSessionState:
        if not data:
            return cls()
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def merge_session_state(
    current: ChatSessionState,
    *,
    goal: str | None = None,
    decision: str | None = None,
    artifact_ids: list[str] | None = None,
    evidence_eids: list[str] | None = None,
    open_loop: str | None = None,
) -> ChatSessionState:
    """Cheap deterministic merge after a turn (LLM updater can replace later)."""
    if goal:
        current.goal = goal.strip()[:400]
    if decision:
        current.decisions = (current.decisions + [decision.strip()[:300]])[-12:]
    if artifact_ids:
        merged = list(dict.fromkeys([*current.artifact_ids_in_focus, *artifact_ids]))
        current.artifact_ids_in_focus = merged[-16:]
    if evidence_eids:
        current.last_evidence_eids = list(dict.fromkeys(evidence_eids))[:16]
    if open_loop:
        current.open_loops = (current.open_loops + [open_loop.strip()[:300]])[-8:]
    return current


def session_state_from_metadata(metadata: dict | None) -> ChatSessionState:
    raw = (metadata or {}).get("session_state")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if isinstance(raw, dict):
        return ChatSessionState.from_json(raw)
    return ChatSessionState()
