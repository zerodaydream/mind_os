"""Phase 04 policy stub. The real policy engine and approvals arrive in Phase 05."""

from __future__ import annotations

from opsagent.domain import PolicyDecision, Principal, RiskClass


class ReadOnlyPolicy:
    """Allow R0. Deny everything else, because no approval path exists yet. Fails closed."""

    def decide(self, *, tool: str, risk: RiskClass, principal: Principal) -> PolicyDecision:
        return PolicyDecision.ALLOW if risk is RiskClass.R0 else PolicyDecision.DENY
