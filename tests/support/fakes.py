"""In-memory fakes for the audit, artifact, usage, and calendar ports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from opsagent.domain import (
    ALLOWED_TRANSITIONS,
    ApprovalRecord,
    ArtifactSlice,
    CalendarEvent,
    Principal,
    RiskClass,
    Usage,
)
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


class MemoryApprovalRepo:
    """In-memory implementation of ApprovalRepository for unit tests."""

    def __init__(self) -> None:
        self.records: dict[str, ApprovalRecord] = {}
        self.transitions: list[tuple[str, str, str]] = []  # (id, from, to)
        self.started_executions: list[tuple[str, str, str | None]] = []

    async def create(
        self,
        *,
        approval_id: str,
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        payload_hash: str,
        idempotency_key: str,
        args_json: str,
        summary: str,
        payload_preview: str,
        tainted: bool,
        expires_at: datetime,
        run_id: str | None,
    ) -> None:
        self.records[approval_id] = ApprovalRecord(
            id=approval_id,
            run_id=run_id,
            tool=tool,
            tool_version=tool_version,
            principal=principal,
            risk=risk,
            state="PENDING",
            payload_hash=payload_hash,
            idempotency_key=idempotency_key,
            args_json=args_json,
            summary=summary,
            payload_preview=payload_preview,
            tainted=tainted,
            expires_at=expires_at.isoformat(),
        )

    async def transition(
        self,
        approval_id: str,
        *,
        from_states: frozenset[str],
        to_state: str,
        actor: str | None = None,
        payload_hash: str | None = None,
        args_json: str | None = None,
    ) -> bool:
        rec = self.records.get(approval_id)
        if rec is None or rec.state not in from_states:
            return False
        if to_state not in ALLOWED_TRANSITIONS[rec.state]:
            raise ValueError(f"invalid approval state transition: {rec.state} -> {to_state}")
        self.transitions.append((approval_id, rec.state, to_state))
        self.records[approval_id] = ApprovalRecord(
            id=rec.id,
            run_id=rec.run_id,
            tool=rec.tool,
            tool_version=rec.tool_version,
            principal=rec.principal,
            risk=rec.risk,
            state=to_state,  # type: ignore[arg-type]
            payload_hash=payload_hash or rec.payload_hash,
            idempotency_key=rec.idempotency_key,
            args_json=args_json or rec.args_json,
            summary=rec.summary,
            payload_preview=rec.payload_preview,
            tainted=rec.tainted,
            expires_at=rec.expires_at,
            actor=actor or rec.actor,
        )
        return True

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)

    async def pending_for_run(self, run_id: str) -> list[ApprovalRecord]:
        return [r for r in self.records.values() if r.run_id == run_id and r.state == "PENDING"]

    async def expire_stale(self) -> int:
        count = 0
        now_iso = datetime.now().isoformat()
        for aid, rec in list(self.records.items()):
            if (
                rec.state in ("PENDING", "APPROVED", "APPROVED_EDITED")
                and rec.expires_at
                and rec.expires_at < now_iso
            ):
                await self.transition(
                    aid,
                    from_states=frozenset({"PENDING", "APPROVED", "APPROVED_EDITED"}),
                    to_state="EXPIRED",
                )
                count += 1
        return count

    async def find_started_executions(self) -> list[tuple[str, str, str | None]]:
        return list(self.started_executions)
