"""Risk, principal, and policy vocabulary (ARCHITECTURE section 5)."""

from __future__ import annotations

from enum import StrEnum


class RiskClass(StrEnum):
    """R0 read-only .. R4 bulk or broadly destructive."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"

    @property
    def rank(self) -> int:
        return int(self.value[1:])


class Principal(StrEnum):
    """Who a tool invocation is attributed to."""

    USER = "USER"
    AGENT = "AGENT"
    JOB = "JOB"
    SANDBOX = "SANDBOX"


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class ApprovalMode(StrEnum):
    """User-selectable session mode. It can only tighten `policy.toml`, never loosen it."""

    DEFAULT = "default"
    PLAN = "plan"


class SessionMode:
    """The active session mode. One holder is shared by the UI facade and the policy engine."""

    def __init__(self, mode: ApprovalMode = ApprovalMode.DEFAULT) -> None:
        self._mode = mode

    @property
    def current(self) -> ApprovalMode:
        return self._mode

    def set(self, mode: ApprovalMode) -> None:
        self._mode = mode
