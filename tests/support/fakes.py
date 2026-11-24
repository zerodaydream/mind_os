"""In-memory fakes for the audit, artifact, usage, calendar, approval, and execution ports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from mindos.domain import (
    ALLOWED_TRANSITIONS,
    ApprovalRecord,
    ArtifactSlice,
    CalendarEvent,
    Principal,
    RiskClass,
    Usage,
)
from mindos.tools import TransientToolError


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


class ManualClock:
    """A clock tests can move. Approval expiry is decided against it."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 11, 23, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


class MemoryApprovalRepo:
    """In-memory `ApprovalRepository` that behaves like the SQL one, including its guards.

    It mirrors: the UNIQUE idempotency key, the legal-transition trigger, the expiry condition
    inside the atomic UPDATE, `actor` kept when a later transition names none, immutable payloads
    after a decision, and `original_payload_hash` set on the first edit.
    """

    def __init__(self, clock: ManualClock | None = None) -> None:
        self.clock = clock or ManualClock()
        self.records: dict[str, ApprovalRecord] = {}
        self.transitions: list[tuple[str, str, str]] = []  # (id, from, to)

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
        thread_id: str | None = None,
        conversation_id: str | None = None,
        tool_call_id: str | None = None,
        agent: str | None = None,
        deferred: bool = False,
    ) -> bool:
        if any(r.idempotency_key == idempotency_key for r in self.records.values()):
            return False
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
            thread_id=thread_id,
            conversation_id=conversation_id,
            tool_call_id=tool_call_id,
            agent=agent,
            deferred=deferred,
        )
        return True

    def _expired(self, rec: ApprovalRecord) -> bool:
        return bool(rec.expires_at) and datetime.fromisoformat(rec.expires_at or "") <= (
            self.clock.now()
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
        require_unexpired: bool = False,
    ) -> bool:
        rec = self.records.get(approval_id)
        if rec is None or rec.state not in from_states:
            return False
        if require_unexpired and self._expired(rec):
            return False
        if to_state not in ALLOWED_TRANSITIONS[rec.state]:
            raise ValueError(f"invalid approval state transition: {rec.state} -> {to_state}")
        if payload_hash is not None and to_state != "APPROVED_EDITED":
            raise ValueError("approval payload is immutable after a decision")
        self.transitions.append((approval_id, rec.state, to_state))
        edited = payload_hash is not None
        self.records[approval_id] = rec.model_copy(
            update={
                "state": to_state,
                "payload_hash": payload_hash or rec.payload_hash,
                "args_json": args_json or rec.args_json,
                "actor": actor or rec.actor,
                "original_payload_hash": (
                    rec.original_payload_hash or rec.payload_hash
                    if edited
                    else rec.original_payload_hash
                ),
            }
        )
        return True

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return self.records.get(approval_id)

    async def get_by_idempotency_key(self, key: str) -> ApprovalRecord | None:
        return next((r for r in self.records.values() if r.idempotency_key == key), None)

    async def expire_stale(self) -> list[ApprovalRecord]:
        stale = [
            r
            for r in self.records.values()
            if r.state in ("PENDING", "APPROVED", "APPROVED_EDITED") and self._expired(r)
        ]
        for rec in stale:
            await self.transition(rec.id, from_states=frozenset({rec.state}), to_state="EXPIRED")
        return [self.records[r.id] for r in stale]

    async def abandon_consumed(self) -> list[ApprovalRecord]:
        stuck = [r for r in self.records.values() if r.state == "CONSUMED"]
        for rec in stuck:
            await self.transition(
                rec.id,
                from_states=frozenset({"CONSUMED"}),
                to_state="EXEC_FAILED",
                actor="RECONCILER",
            )
        return [self.records[r.id] for r in stuck]

    async def undelivered(self) -> list[ApprovalRecord]:
        return [r for r in self.records.values() if not r.delivered and r.thread_id]

    async def mark_delivered(self, approval_id: str) -> None:
        rec = self.records.get(approval_id)
        if rec is not None:
            self.records[approval_id] = rec.model_copy(update={"delivered": True})


class MemoryExecutionLog:
    """In-memory `ExecutionLog` with the same unique-key rule as `tool_executions`."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def start(
        self,
        *,
        execution_id: str,
        run_id: str | None,
        tool: str,
        agent: str,
        principal: Principal,
        risk: RiskClass,
        idempotency_key: str,
        args_hash: str,
    ) -> bool:
        if any(r["key"] == idempotency_key for r in self.rows.values()):
            return False
        self.rows[execution_id] = {
            "tool": tool,
            "run_id": run_id,
            "key": idempotency_key,
            "state": "STARTED",
        }
        return True

    async def finish(self, execution_id: str, *, ok: bool) -> None:
        row = self.rows.get(execution_id)
        if row is not None and row["state"] == "STARTED":
            row["state"] = "FINISHED" if ok else "FAILED"

    async def abandon_started(self) -> list[tuple[str, str, str | None]]:
        out = []
        for eid, row in self.rows.items():
            if row["state"] == "STARTED":
                row["state"] = "UNKNOWN"
                out.append((eid, row["tool"], row["run_id"]))
        return out
