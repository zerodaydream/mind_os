"""Approval domain types: state machine, payload canonicalization, and taint (ARCHITECTURE §5)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from opsagent.domain.risk import Principal, RiskClass

ApprovalState = Literal[
    "PENDING",
    "APPROVED",
    "APPROVED_EDITED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "CONSUMED",
    "EXECUTED",
    "EXEC_FAILED",
    "INVALIDATED",
]

# Allowed forward transitions for the state machine
ALLOWED_TRANSITIONS: MappingProxyType[ApprovalState, frozenset[ApprovalState]] = MappingProxyType(
    dict(
        [
            (
                "PENDING",
                frozenset({"APPROVED", "APPROVED_EDITED", "REJECTED", "EXPIRED", "CANCELLED"}),
            ),
            ("APPROVED", frozenset({"CONSUMED", "EXPIRED", "INVALIDATED"})),
            ("APPROVED_EDITED", frozenset({"CONSUMED", "EXPIRED", "INVALIDATED"})),
            ("REJECTED", frozenset()),
            ("EXPIRED", frozenset()),
            ("CANCELLED", frozenset()),
            ("CONSUMED", frozenset({"EXECUTED", "EXEC_FAILED", "INVALIDATED"})),
            ("EXECUTED", frozenset()),
            ("EXEC_FAILED", frozenset()),
            ("INVALIDATED", frozenset()),
        ]
    )
)

TERMINAL_STATES: frozenset[ApprovalState] = frozenset(
    {"REJECTED", "EXPIRED", "CANCELLED", "EXECUTED", "EXEC_FAILED", "INVALIDATED"}
)

# TTL per risk class
TTL: MappingProxyType[RiskClass, timedelta] = MappingProxyType(
    dict(
        [
            (RiskClass.R0, timedelta(hours=1)),
            (RiskClass.R1, timedelta(hours=4)),
            (RiskClass.R2, timedelta(hours=4)),
            (RiskClass.R3, timedelta(hours=2)),
            (RiskClass.R4, timedelta(minutes=30)),
        ]
    )
)


class ApprovalRecord(BaseModel):
    """An approval row, mirroring the DB record (read model)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    run_id: str | None
    tool: str
    tool_version: str
    principal: Principal
    risk: RiskClass
    state: ApprovalState
    payload_hash: str
    idempotency_key: str
    args_json: str
    summary: str
    payload_preview: str
    tainted: bool = False
    # expires_at and actor as strings (UTC ISO) to stay pure
    expires_at: str | None = None
    actor: str | None = None


def canonical_payload(
    tool: str, tool_version: str, principal: str, args: dict[str, Any]
) -> str:
    """Canonical JSON for hashing: sorted keys, no whitespace."""
    obj = {"tool": tool, "tool_version": tool_version, "principal": principal, "args": args}
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(tool: str, tool_version: str, principal: str, args: dict[str, Any]) -> str:
    """SHA-256 hex digest of the canonical payload."""
    return hashlib.sha256(
        canonical_payload(tool, tool_version, principal, args).encode()
    ).hexdigest()


def idempotency_key(run_id: str | None, tool_call_id: str, ph: str) -> str:
    """Deterministic key that prevents duplicate approvals for the same call."""
    raw = json.dumps(
        {"run_id": run_id, "tool_call_id": tool_call_id, "payload_hash": ph}, sort_keys=True
    )
    return hashlib.sha256(raw.encode()).hexdigest()
