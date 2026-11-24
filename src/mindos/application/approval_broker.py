"""Approval broker: creates, tracks, and resolves approval rows (ARCHITECTURE section 5).

The engine calls `request()` when policy says REQUIRE_APPROVAL; the graph interrupts. The facade
calls `resolve()` with the user's decision. The engine calls `consume()` before executing.
Nothing here can bypass the engine's re-check at execution time, and every transition is a
single atomic repository call whose result is checked, never assumed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from types import MappingProxyType
from typing import Any

from mindos.domain import (
    APPROVAL_TTL,
    ApprovalRecord,
    ApprovalTicket,
    AuditSink,
    BoundState,
    Clock,
    EditValidator,
    IdFactory,
    Principal,
    Resolution,
    RiskClass,
    approval_idempotency_key,
    approval_payload_hash,
    current,
    events,
)
from mindos.domain.ports import ApprovalRepository

log = logging.getLogger(__name__)

_PREVIEW_CHARS = 2000
_LIVE = frozenset({"APPROVED", "APPROVED_EDITED"})

_SUMMARY: MappingProxyType[str, str] = MappingProxyType(
    dict(
        [
            ("mail.send_message", "Send email"),
            ("calendar.create_event", "Create calendar event"),
            ("calendar.update_event", "Update calendar event"),
            ("calendar.delete_event", "Delete calendar event"),
            ("files.delete", "Delete file"),
            ("files.edit", "Edit file"),
            ("system.run_action", "Run system action"),
        ]
    )
)


def _canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)


class ApprovalBroker:
    """Creates, tracks, and resolves approval requests."""

    def __init__(
        self,
        *,
        repo: ApprovalRepository,
        audit: AuditSink,
        new_id: IdFactory,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._new_id = new_id
        self._clock = clock
        self._validator: EditValidator | None = None

    def bind_validator(self, validator: EditValidator) -> None:
        """The engine validates edits, and is built after the broker, so it is attached late."""
        if self._validator is not None:
            raise RuntimeError("edit validator already bound")
        self._validator = validator

    # ---- creation --------------------------------------------------------------------------
    async def request(
        self,
        *,
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        args: dict[str, Any],
        run_id: str | None,
        tool_call_id: str,
        tainted: bool = False,
        bound: BoundState | None = None,
        agent: str | None = None,
        deferred: bool = False,
    ) -> ApprovalTicket:
        """Create a PENDING approval, or return the one a replay of this call already made.

        The key names the call by its arguments alone; the stored hash also covers `bound`.
        """
        bound = bound or BoundState()
        ph = approval_payload_hash(tool, tool_version, principal.value, args, bound)
        idem = approval_idempotency_key(
            run_id, tool_call_id, approval_payload_hash(tool, tool_version, principal.value, args)
        )
        ids = current()
        expires_at = self._clock.now() + APPROVAL_TTL[risk]
        approval_id = self._new_id("apr")
        summary = _SUMMARY.get(tool, tool.replace(".", " ").title())
        shown = {**args, "_resolved_targets": bound.resolved_targets} if bound else args
        preview = json.dumps(shown, indent=2, default=str)[:_PREVIEW_CHARS]

        created = await self._repo.create(
            approval_id=approval_id,
            tool=tool,
            tool_version=tool_version,
            principal=principal,
            risk=risk,
            payload_hash=ph,
            idempotency_key=idem,
            args_json=_canonical_args(args),
            summary=summary,
            payload_preview=preview,
            tainted=tainted,
            expires_at=expires_at,
            run_id=run_id,
            thread_id=ids.get("thread_id"),
            conversation_id=ids.get("conversation_id"),
            tool_call_id=tool_call_id,
            agent=agent,
            deferred=deferred,
        )
        if not created:
            existing = await self._repo.get_by_idempotency_key(idem)
            if existing is None:  # the key exists, so the row must; anything else is corruption
                raise RuntimeError(f"approval idempotency key {idem} vanished")
            return ApprovalTicket(view=view_of(existing), created=False)

        await self._audit.append(
            event_type="approval.created",
            actor=principal.value,
            subject_type="approval",
            subject_id=approval_id,
            data={"tool": tool, "risk": risk.value, "payload_hash": ph, "tainted": tainted},
        )
        view = events.ApprovalView(
            approval_id=approval_id,
            tool=tool,
            risk=risk,
            summary=summary,
            payload_preview=preview,
            payload_hash=ph,
            tainted=tainted,
            expires_at=expires_at,
            deferred=deferred,
        )
        return ApprovalTicket(view=view, created=True)

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return await self._repo.get(approval_id)

    async def existing(
        self,
        *,
        tool: str,
        tool_version: str,
        principal: Principal,
        args: dict[str, Any],
        run_id: str | None,
        tool_call_id: str,
    ) -> ApprovalRecord | None:
        ph = approval_payload_hash(tool, tool_version, principal.value, args)
        return await self._repo.get_by_idempotency_key(
            approval_idempotency_key(run_id, tool_call_id, ph)
        )

    # ---- decisions -------------------------------------------------------------------------
    async def resolve(
        self,
        approval_id: str,
        *,
        decision: str,
        edited_args: dict[str, Any] | None = None,
        comment: str | None = None,
        actor: str = "USER",
    ) -> Resolution:
        """Apply a user decision and report what actually happened."""
        if decision == "approve":
            return await self.approve(approval_id, actor=actor)
        if decision == "edit":
            return await self.approve_edited(approval_id, new_args=edited_args or {}, actor=actor)
        if decision == "reject":
            return await self.reject(approval_id, reason=comment, actor=actor)
        return Resolution(outcome="unavailable", detail=f"unknown decision {decision!r}")

    async def approve(self, approval_id: str, *, actor: str = "USER") -> Resolution:
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED",
            actor=actor,
            require_unexpired=True,
        )
        if not ok:
            return await self._why_not(approval_id)
        await self._audit_event("approval.approved", approval_id, actor)
        return Resolution(outcome="approved")

    async def approve_edited(
        self, approval_id: str, *, new_args: dict[str, Any], actor: str = "USER"
    ) -> Resolution:
        """Re-validate and re-classify edited arguments, then re-hash. An edit may not raise risk.

        On refusal the approval stays PENDING, so the user can edit again, approve the original
        payload, or reject.
        """
        record = await self._repo.get(approval_id)
        if record is None or record.state != "PENDING":
            return await self._why_not(approval_id)
        if self._validator is None:
            return Resolution(outcome="refused", detail="edits cannot be validated right now")
        verdict = await self._validator.revalidate(
            tool=record.tool, principal=record.principal, args=new_args
        )
        if not verdict.ok or verdict.risk is None:
            return Resolution(outcome="refused", detail=verdict.reason or "invalid arguments")
        if verdict.risk.rank > record.risk.rank:
            return Resolution(
                outcome="refused",
                detail=(
                    f"this edit raises the risk from {record.risk.value} to {verdict.risk.value}; "
                    "reject it and let the agent ask again"
                ),
            )
        new_ph = approval_payload_hash(
            record.tool,
            record.tool_version,
            record.principal.value,
            verdict.normalized_args,
            verdict.bound,
        )
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED_EDITED",
            actor=actor,
            payload_hash=new_ph,
            args_json=_canonical_args(verdict.normalized_args),
            require_unexpired=True,
        )
        if not ok:
            return await self._why_not(approval_id)
        await self._audit_event(
            "approval.edited",
            approval_id,
            actor,
            {"original_payload_hash": record.payload_hash, "new_payload_hash": new_ph},
        )
        return Resolution(outcome="edited")

    async def reject(
        self, approval_id: str, *, reason: str | None = None, actor: str = "USER"
    ) -> Resolution:
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="REJECTED",
            actor=actor,
        )
        if not ok:
            return await self._why_not(approval_id)
        await self._audit_event("approval.rejected", approval_id, actor, {"reason": reason})
        return Resolution(outcome="rejected", detail=reason)

    async def _why_not(self, approval_id: str) -> Resolution:
        """A decision did not apply. Say why, and settle an expiry the sweep has not reached."""
        record = await self._repo.get(approval_id)
        if record is None:
            return Resolution(outcome="unavailable", detail="no such approval")
        if record.state == "EXPIRED" or (record.state == "PENDING" and self._past(record)):
            if record.state == "PENDING" and await self._repo.transition(
                approval_id, from_states=frozenset({"PENDING"}), to_state="EXPIRED"
            ):
                await self._audit_event("approval.expired", approval_id, "ENGINE")
            return Resolution(outcome="expired", detail="the approval expired")
        return Resolution(outcome="unavailable", detail=f"already {record.state.lower()}")

    def _past(self, record: ApprovalRecord) -> bool:
        return bool(
            record.expires_at and datetime.fromisoformat(record.expires_at) <= self._clock.now()
        )

    # ---- execution -------------------------------------------------------------------------
    async def consume(self, approval_id: str) -> bool:
        """Atomically claim an unexpired approval for execution. True only for the one winner."""
        ok = await self._repo.transition(
            approval_id,
            from_states=_LIVE,
            to_state="CONSUMED",
            require_unexpired=True,
        )
        if ok:
            await self._audit_event("approval.consumed", approval_id, "ENGINE")
        return ok

    async def mark_executed(self, approval_id: str, *, success: bool) -> None:
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"CONSUMED"}),
            to_state="EXECUTED" if success else "EXEC_FAILED",
        )
        if ok:
            await self._audit_event(
                "approval.executed" if success else "approval.exec_failed",
                approval_id,
                "ENGINE",
            )

    async def invalidate(self, approval_id: str, *, reason: str) -> None:
        ok = await self._repo.transition(
            approval_id,
            from_states=_LIVE | {"CONSUMED"},
            to_state="INVALIDATED",
        )
        if ok:
            await self._audit_event(
                "approval.invalidated", approval_id, "ENGINE", {"reason": reason}
            )

    # ---- lifecycle -------------------------------------------------------------------------
    async def cancel_for_run(self, run_id: str) -> int:
        """Cancelling a run cancels its pending approvals and stops redelivering the rest."""
        cancelled = 0
        for record in [r for r in await self._repo.undelivered() if r.run_id == run_id]:
            if await self._repo.transition(
                record.id, from_states=frozenset({"PENDING"}), to_state="CANCELLED", actor="USER"
            ):
                cancelled += 1
                await self._audit_event("approval.cancelled", record.id, "USER")
            await self._repo.mark_delivered(record.id)
        return cancelled

    async def expire_stale(self) -> list[ApprovalRecord]:
        expired = await self._repo.expire_stale()
        for record in expired:
            await self._audit_event("approval.expired", record.id, "ENGINE")
        return expired

    async def abandon_consumed(self) -> list[ApprovalRecord]:
        """Settle approvals whose execution was cut off. The outcome is unknown, and audited so."""
        stuck = await self._repo.abandon_consumed()
        for record in stuck:
            await self._audit_event(
                "approval.outcome_unknown", record.id, "RECONCILER", {"tool": record.tool}
            )
        return stuck

    async def undelivered(self) -> list[ApprovalRecord]:
        return await self._repo.undelivered()

    async def mark_delivered(self, approval_id: str) -> None:
        await self._repo.mark_delivered(approval_id)

    async def _audit_event(
        self, event_type: str, approval_id: str, actor: str, data: dict[str, Any] | None = None
    ) -> None:
        await self._audit.append(
            event_type=event_type,
            actor=actor,
            subject_type="approval",
            subject_id=approval_id,
            data=data,
        )


def view_of(record: ApprovalRecord) -> events.ApprovalView:
    return events.ApprovalView(
        approval_id=record.id,
        tool=record.tool,
        risk=record.risk,
        summary=record.summary,
        payload_preview=record.payload_preview,
        payload_hash=record.payload_hash,
        tainted=record.tainted,
        expires_at=datetime.fromisoformat(record.expires_at) if record.expires_at else None,
        deferred=record.deferred,
    )
