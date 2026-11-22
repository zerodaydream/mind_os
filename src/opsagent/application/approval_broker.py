"""Approval broker: creates, tracks, and resolves approval rows (ARCHITECTURE §5).

The engine calls `request()` when policy says REQUIRE_APPROVAL; the graph interrupts.
The UI calls `decide()` with the user's decision. The engine calls `consume()` before execution.
Nothing here can bypass the engine's hash re-check at execution time.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any

from opsagent.domain import (
    APPROVAL_TTL,
    AuditSink,
    IdFactory,
    Principal,
    RiskClass,
    approval_idempotency_key,
    approval_payload_hash,
)
from opsagent.domain.approvals import ApprovalRecord
from opsagent.domain.events import ApprovalView
from opsagent.domain.ports import ApprovalRepository

log = logging.getLogger(__name__)

_SUMMARY: MappingProxyType[str, str] = MappingProxyType(
    dict(
        [
            ("mail.send_message", "Send email"),
            ("calendar.create_event", "Create calendar event"),
            ("calendar.update_event", "Update calendar event"),
            ("calendar.delete_event", "Delete calendar event"),
            ("teams.send_message", "Send Teams message"),
            ("files.delete", "Delete file"),
            ("files.edit", "Edit file"),
            ("system.run_action", "Run system action"),
        ]
    )
)


class ApprovalBroker:
    """Creates, tracks, and resolves approval requests."""

    def __init__(
        self,
        *,
        repo: ApprovalRepository,
        audit: AuditSink,
        new_id: IdFactory,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._new_id = new_id

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
    ) -> ApprovalView:
        """Create an approval row (PENDING) and return its view for the UI."""
        ph = approval_payload_hash(tool, tool_version, principal.value, args)
        idem = approval_idempotency_key(run_id, tool_call_id, ph)
        approval_id = self._new_id("apr")
        ttl = APPROVAL_TTL[risk]
        expires_at = datetime.now(UTC) + ttl
        summary = _SUMMARY.get(tool, tool.replace(".", " ").title())
        preview = json.dumps(args, indent=2, default=str)[:2000]

        await self._repo.create(
            approval_id=approval_id,
            tool=tool,
            tool_version=tool_version,
            principal=principal,
            risk=risk,
            payload_hash=ph,
            idempotency_key=idem,
            args_json=json.dumps(args, default=str),
            summary=summary,
            payload_preview=preview,
            tainted=tainted,
            expires_at=expires_at,
            run_id=run_id,
        )
        await self._audit.append(
            event_type="approval.created",
            actor=principal.value,
            subject_type="approval",
            subject_id=approval_id,
            data={"tool": tool, "risk": risk.value, "payload_hash": ph},
        )
        return ApprovalView(
            approval_id=approval_id,
            tool=tool,
            risk=risk,
            summary=summary,
            payload_preview=preview,
            payload_hash=ph,
            tainted=tainted,
            expires_at=expires_at,
        )

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        """Fetch an approval record by ID."""
        return await self._repo.get(approval_id)

    async def approve(self, approval_id: str, *, actor: str = "USER") -> bool:
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED",
            actor=actor,
        )
        if ok:
            await self._audit.append(
                event_type="approval.approved",
                actor=actor,
                subject_type="approval",
                subject_id=approval_id,
            )
        return ok

    async def approve_edited(
        self,
        approval_id: str,
        *,
        new_args: dict[str, Any],
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        actor: str = "USER",
    ) -> str | None:
        """Re-validate and re-hash edited args. Returns new payload_hash or None on failure."""
        new_ph = approval_payload_hash(tool, tool_version, principal.value, new_args)
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED_EDITED",
            actor=actor,
            payload_hash=new_ph,
            args_json=json.dumps(new_args, default=str),
        )
        if not ok:
            return None
        await self._audit.append(
            event_type="approval.edited",
            actor=actor,
            subject_type="approval",
            subject_id=approval_id,
            data={"new_payload_hash": new_ph},
        )
        return new_ph

    async def reject(
        self, approval_id: str, *, reason: str | None = None, actor: str = "USER"
    ) -> bool:
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"PENDING"}),
            to_state="REJECTED",
            actor=actor,
        )
        if ok:
            await self._audit.append(
                event_type="approval.rejected",
                actor=actor,
                subject_type="approval",
                subject_id=approval_id,
                data={"reason": reason},
            )
        return ok

    async def consume(self, approval_id: str) -> bool:
        """Atomically claim an approved approval for execution. Returns True if claimed."""
        ok = await self._repo.transition(
            approval_id,
            from_states=frozenset({"APPROVED", "APPROVED_EDITED"}),
            to_state="CONSUMED",
        )
        if ok:
            await self._audit.append(
                event_type="approval.consumed",
                actor="ENGINE",
                subject_type="approval",
                subject_id=approval_id,
            )
        return ok

    async def mark_executed(self, approval_id: str, *, success: bool) -> None:
        to_state = "EXECUTED" if success else "EXEC_FAILED"
        await self._repo.transition(
            approval_id, from_states=frozenset({"CONSUMED"}), to_state=to_state
        )

    async def invalidate(self, approval_id: str) -> None:
        await self._repo.transition(
            approval_id,
            from_states=frozenset({"APPROVED", "APPROVED_EDITED", "CONSUMED"}),
            to_state="INVALIDATED",
        )
        await self._audit.append(
            event_type="approval.invalidated",
            actor="ENGINE",
            subject_type="approval",
            subject_id=approval_id,
        )

    async def expire_stale(self) -> int:
        return await self._repo.expire_stale()
