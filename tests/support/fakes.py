"""In-memory fakes for the audit, artifact, usage, and calendar ports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from opsagent.domain import ArtifactSlice, CalendarEvent, Usage
from opsagent.tools import TransientToolError


class MemoryAudit:
    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def append(
        self,
        *,
        event_type: str,
        actor: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        if self.fail:
            raise RuntimeError("audit down")
        self.rows.append(
            {
                "event_type": event_type,
                "actor": actor,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "data": data or {},
            }
        )
        return len(self.rows)

    def types(self) -> list[str]:
        return [r["event_type"] for r in self.rows]


class MemoryArtifacts:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    async def put(self, content: str, *, media_type: str = "text/plain") -> str:
        artifact_id = f"art_{len(self.items) + 1:026d}"
        self.items[artifact_id] = content
        return artifact_id

    async def read(self, artifact_id: str, *, offset: int, limit: int) -> ArtifactSlice | None:
        text = self.items.get(artifact_id)
        if text is None:
            return None
        return ArtifactSlice(text=text[offset : offset + limit], offset=offset, total=len(text))


class MemoryUsage:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        role: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
        cost_usd: float | None,
    ) -> None:
        self.rows.append({"role": role, "model": model, "usage": usage, "cost": cost_usd})


class MemoryCalendar:
    def __init__(self, events: list[CalendarEvent]) -> None:
        self.events = events
        self.calls: list[dict[str, Any]] = []
        self.fail_times = 0  # raise a transient error this many times first

    async def search_events(
        self, *, start: datetime, end: datetime, query: str | None, limit: int
    ) -> list[CalendarEvent]:
        self.calls.append({"start": start, "end": end, "query": query, "limit": limit})
        if self.fail_times > 0:
            self.fail_times -= 1
            raise TransientToolError("rate limited")
        hits = [e for e in self.events if e.start < end and e.end > start]
        if query:
            hits = [e for e in hits if query.lower() in e.title.lower()]
        return sorted(hits, key=lambda e: e.start)[:limit]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        return next((e for e in self.events if e.id == event_id), None)
