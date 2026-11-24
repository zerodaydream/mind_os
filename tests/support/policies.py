"""Policies for tests that need a fixed, simple answer."""

from __future__ import annotations

from mindos.domain import PolicyDecision, Principal, RiskClass


class ReadOnlyPolicy:
    """Allow R0, deny everything else."""

    def decide(
        self, *, tool: str, risk: RiskClass, principal: Principal, tainted: bool = False
    ) -> PolicyDecision:
        return PolicyDecision.ALLOW if risk is RiskClass.R0 else PolicyDecision.DENY
