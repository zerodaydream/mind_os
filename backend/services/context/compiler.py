"""Compile typed segments into a budgeted prompt (stable prefix first)."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.services.agents.core.contracts.evidence import EvidencePack
from backend.services.context.segments import (
    DEFAULT_BUDGETS,
    EVICT_ORDER,
    SEGMENT_ORDER,
    PromptSegment,
    SegmentKind,
)


@dataclass
class CompiledPrompt:
    """Compiled prompt plus diagnostics for turn_log."""

    text: str
    segments: list[PromptSegment] = field(default_factory=list)
    total_tokens: int = 0
    evicted: list[str] = field(default_factory=list)


class ContextCompiler:
    """Assemble prompt segments under a token budget; never drop the task."""

    def __init__(self, *, total_budget_tokens: int = 8_000) -> None:
        self.total_budget_tokens = total_budget_tokens

    def compile(self, segments: list[PromptSegment]) -> CompiledPrompt:
        by_kind: dict[SegmentKind, list[PromptSegment]] = {k: [] for k in SEGMENT_ORDER}
        for seg in segments:
            if not (seg.text or "").strip():
                continue
            budget = seg.budget_tokens if seg.budget_tokens is not None else DEFAULT_BUDGETS[seg.kind]
            text = _clip_to_tokens(seg.text.strip(), budget)
            by_kind[seg.kind].append(
                PromptSegment(kind=seg.kind, text=text, budget_tokens=budget)
            )

        ordered: list[PromptSegment] = []
        for kind in SEGMENT_ORDER:
            ordered.extend(by_kind[kind])

        evicted: list[str] = []
        while _total_tokens(ordered) > self.total_budget_tokens:
            if not _evict_one(ordered, evicted):
                break

        parts = [s.text for s in ordered if s.text]
        text = "\n\n".join(parts)
        return CompiledPrompt(
            text=text,
            segments=ordered,
            total_tokens=_total_tokens(ordered),
            evicted=evicted,
        )

    def compile_turn(
        self,
        *,
        core_system: str,
        domain_modules: list[str] | None = None,
        user_profile: str = "",
        session_state: str = "",
        evidence: EvidencePack | None = None,
        recent_turns: str = "",
        task: str,
    ) -> CompiledPrompt:
        segs: list[PromptSegment] = [
            PromptSegment(kind="core_system", text=core_system),
        ]
        for mod in domain_modules or []:
            segs.append(PromptSegment(kind="domain", text=mod))
        if user_profile:
            segs.append(PromptSegment(kind="user_profile", text=user_profile))
        if session_state:
            segs.append(PromptSegment(kind="session_state", text=session_state))
        if evidence and evidence.items:
            segs.append(
                PromptSegment(kind="evidence", text=format_evidence_block(evidence))
            )
        if recent_turns:
            segs.append(PromptSegment(kind="recent_turns", text=recent_turns))
        segs.append(PromptSegment(kind="task", text=task))
        return self.compile(segs)


def format_evidence_block(pack: EvidencePack) -> str:
    """Best item first, second-best last (lost-in-middle mitigation)."""
    items = list(pack.items)
    if not items:
        return ""
    if len(items) == 1:
        ordered = items
    else:
        ordered = [items[0], *items[2:], items[1]]
    lines = ["Evidence (cite as [E#]):"]
    for ev in ordered:
        title = ev.title or ev.kind
        header = f"[{ev.eid}] {title}"
        if ev.uri:
            header += f" · {ev.uri}"
        lines.append(header)
        lines.append(ev.text.strip())
        lines.append("")
    return "\n".join(lines).rstrip()


def _clip_to_tokens(text: str, budget: int) -> str:
    max_chars = max(16, budget * 4)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _total_tokens(segments: list[PromptSegment]) -> int:
    return sum(s.estimate_tokens() for s in segments)


def _evict_one(segments: list[PromptSegment], evicted: list[str]) -> bool:
    for kind in EVICT_ORDER:
        for i in range(len(segments) - 1, -1, -1):
            if segments[i].kind == kind:
                removed = segments.pop(i)
                evicted.append(removed.kind)
                return True
    return False
