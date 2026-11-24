"""Approval domain types: state machine, payload canonicalization, and taint (ARCHITECTURE §5)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from opsagent.domain.events import ApprovalView
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
    # Where the run is paused, so a decision can be re-delivered after a restart.
    thread_id: str | None = None
    conversation_id: str | None = None
    tool_call_id: str | None = None
    original_payload_hash: str | None = None  # set when the user edited the payload
    delivered: bool = False  # the paused graph has received the decision
    # Raised by a subagent, which cannot pause. The run does not wait: the application executes
    # the approved call itself, as `agent`, once the user decides (ADR 0005).
    agent: str | None = None
    deferred: bool = False


class ApprovalTicket(BaseModel):
    """Result of asking for an approval. `created` is False when the request was a replay.

    LangGraph re-runs a tool node from the top on resume, so asking again must return the same
    approval instead of creating a second one, and must not announce it to the user again.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    view: ApprovalView
    created: bool


ResolutionOutcome = Literal["approved", "edited", "rejected", "expired", "unavailable", "refused"]


class Resolution(BaseModel):
    """What happened to a user's decision.

    `approved`/`edited`/`rejected` are recorded and the run may be resumed. `expired` means the
    TTL passed first. `unavailable` means the approval was already resolved (a double click or a
    stale dialog). `refused` means an edit was rejected by re-validation; the approval stays
    pending so the user can try again or reject.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ResolutionOutcome
    detail: str | None = None

    @property
    def recorded(self) -> bool:
        return self.outcome in ("approved", "edited", "rejected")


class BoundState(BaseModel):
    """What the user's approval is tied to besides the arguments (ARCHITECTURE 5.4).

    `resolved_targets` maps what the arguments named to what it resolved to (a path to its real
    path, a name to an ID). `base_state_hashes` maps each target to a hash of its current state.
    If either differs at execution time the approval no longer describes the action.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resolved_targets: dict[str, str] = {}
    base_state_hashes: dict[str, str] = {}

    def __bool__(self) -> bool:
        return bool(self.resolved_targets or self.base_state_hashes)


class EditVerdict(BaseModel):
    """The engine's answer to "may these edited arguments replace the approved ones?"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    reason: str | None = None
    risk: RiskClass | None = None
    normalized_args: dict[str, Any] = {}
    bound: BoundState = BoundState()


def canonical_payload(
    tool: str,
    tool_version: str,
    principal: str,
    args: dict[str, Any],
    bound: BoundState | None = None,
) -> str:
    """Canonical JSON for hashing: sorted keys, no whitespace.

    `bound` joins the payload only when it carries something, so a tool with no resolved
    targets or base state hashes exactly as before.
    """
    obj: dict[str, Any] = {
        "tool": tool,
        "tool_version": tool_version,
        "principal": principal,
        "args": args,
    }
    if bound:
        obj["bound"] = bound.model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def payload_hash(
    tool: str,
    tool_version: str,
    principal: str,
    args: dict[str, Any],
    bound: BoundState | None = None,
) -> str:
    """SHA-256 hex digest of the canonical payload."""
    return hashlib.sha256(
        canonical_payload(tool, tool_version, principal, args, bound).encode()
    ).hexdigest()


def idempotency_key(run_id: str | None, tool_call_id: str, ph: str) -> str:
    """Deterministic key that prevents duplicate approvals for the same call.

    `ph` is the hash of the arguments alone (no `BoundState`): the call keeps its identity even
    if the state it was bound to drifts, and the drift is caught when the approval is verified.
    """
    raw = json.dumps(
        {"run_id": run_id, "tool_call_id": tool_call_id, "payload_hash": ph}, sort_keys=True
    )
    return hashlib.sha256(raw.encode()).hexdigest()
