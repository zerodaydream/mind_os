"""Policy engine: decides whether a classified tool call may run (ARCHITECTURE §5.2).

The model has no policy tool. Policy lives in user-owned configuration or compiled defaults.
The engine can only escalate; nothing at runtime lowers it. Fail-closed: unknown tools → DENY.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field

from opsagent.domain import PolicyDecision, Principal, RiskClass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolPolicy:
    """Per-tool policy override."""

    tool: str
    decision: PolicyDecision


@dataclass
class PolicyConfig:
    """Runtime-loaded policy. Assembled from policy.toml + compiled defaults."""

    # R0 auto-allow; R1 configurable; R2+ require approval by default
    r0_auto: bool = True
    r1_auto: bool = True  # drafts, tentative events
    r2_requires_approval: bool = True
    r3_requires_approval: bool = True
    r4_requires_approval: bool = True
    # Per-tool overrides (can only tighten, never loosen R2+)
    overrides: list[ToolPolicy] = field(default_factory=list)

    @classmethod
    def from_toml(cls, toml_str: str) -> PolicyConfig:
        data = tomllib.loads(toml_str)
        rules = data.get("policy", {})
        r0 = rules.get("r0_auto", True)
        r1 = rules.get("r1_auto", True)
        r2 = rules.get("r2_requires_approval", True)
        r3 = rules.get("r3_requires_approval", True)
        r4 = rules.get("r4_requires_approval", True)

        tools_dict = data.get("tools", {})
        overrides: list[ToolPolicy] = []

        def _extract(prefix: str, sub: dict[str, object]) -> None:
            if "decision" in sub or "require_approval" in sub or "deny" in sub:
                decision_str = sub.get("decision")
                if decision_str:
                    try:
                        decision = PolicyDecision(str(decision_str).upper())
                        overrides.append(ToolPolicy(tool=prefix, decision=decision))
                    except ValueError:
                        pass
                elif sub.get("deny"):
                    overrides.append(ToolPolicy(tool=prefix, decision=PolicyDecision.DENY))
                elif sub.get("require_approval"):
                    overrides.append(
                        ToolPolicy(tool=prefix, decision=PolicyDecision.REQUIRE_APPROVAL)
                    )
            else:
                for k, v in sub.items():
                    if isinstance(v, dict):
                        _extract(f"{prefix}.{k}" if prefix else k, v)

        for tool_name, tool_cfg in tools_dict.items():
            if isinstance(tool_cfg, dict):
                _extract(tool_name, tool_cfg)
        return cls(
            r0_auto=r0,
            r1_auto=r1,
            r2_requires_approval=r2,
            r3_requires_approval=r3,
            r4_requires_approval=r4,
            overrides=overrides,
        )

    def to_toml(self) -> str:
        lines = [
            "# User policy configuration (ARCHITECTURE §5.2)",
            "[policy]",
            f"r0_auto = {str(self.r0_auto).lower()}",
            f"r1_auto = {str(self.r1_auto).lower()}",
            f"r2_requires_approval = {str(self.r2_requires_approval).lower()}",
            f"r3_requires_approval = {str(self.r3_requires_approval).lower()}",
            f"r4_requires_approval = {str(self.r4_requires_approval).lower()}",
            "",
        ]
        if self.overrides:
            for override in self.overrides:
                escaped_tool = f'"{override.tool}"' if "." in override.tool else override.tool
                lines.append(f"[tools.{escaped_tool}]")
                lines.append(f'decision = "{override.decision.value}"')
                lines.append("")
        return "\n".join(lines)


class PolicyEngine:
    """Decides whether a classified tool call may run.

    Tightening-only: session mode and taint can raise but never lower the decision.
    Model cannot observe or modify policy.
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config or PolicyConfig()
        self._overrides = {o.tool: o.decision for o in self._config.overrides}

    def decide(
        self,
        *,
        tool: str,
        risk: RiskClass,
        principal: Principal,
        tainted: bool = False,
    ) -> PolicyDecision:
        """Return ALLOW | REQUIRE_APPROVAL | DENY.

        Taint escalates: a tainted run cannot auto-allow anything above R0.
        Principal SANDBOX gets the same treatment as AGENT.
        """
        # Per-tool overrides (user-configured; can only tighten)
        override = self._overrides.get(tool)
        if override is PolicyDecision.DENY:
            return PolicyDecision.DENY

        cfg = self._config
        base = self._base_decision(risk, cfg)

        # Taint escalation: auto-allow → require approval for R1+ in a tainted run
        if tainted and base is PolicyDecision.ALLOW and risk.rank >= 1:
            log.debug("policy: taint escalates %s to REQUIRE_APPROVAL", tool)
            return PolicyDecision.REQUIRE_APPROVAL

        # Per-tool override can only raise to REQUIRE_APPROVAL, never lower to ALLOW
        if override is PolicyDecision.REQUIRE_APPROVAL and base is PolicyDecision.ALLOW:
            return PolicyDecision.REQUIRE_APPROVAL

        return base

    @staticmethod
    def _base_decision(risk: RiskClass, cfg: PolicyConfig) -> PolicyDecision:
        if risk is RiskClass.R0:
            return PolicyDecision.ALLOW if cfg.r0_auto else PolicyDecision.REQUIRE_APPROVAL
        if risk is RiskClass.R1:
            return PolicyDecision.ALLOW if cfg.r1_auto else PolicyDecision.REQUIRE_APPROVAL
        if risk is RiskClass.R2:
            return (
                PolicyDecision.DENY
                if not cfg.r2_requires_approval
                else PolicyDecision.REQUIRE_APPROVAL
            )
        if risk is RiskClass.R3:
            return (
                PolicyDecision.DENY
                if not cfg.r3_requires_approval
                else PolicyDecision.REQUIRE_APPROVAL
            )
        # R4
        return (
            PolicyDecision.DENY
            if not cfg.r4_requires_approval
            else PolicyDecision.REQUIRE_APPROVAL
        )


class ReadOnlyPolicy:
    """Allow R0. Deny everything else. Used in tests and before Phase 05 wires up PolicyEngine."""

    def decide(
        self, *, tool: str, risk: RiskClass, principal: Principal, tainted: bool = False
    ) -> PolicyDecision:
        return PolicyDecision.ALLOW if risk is RiskClass.R0 else PolicyDecision.DENY
