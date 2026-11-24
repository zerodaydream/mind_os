"""Policy engine: decides whether a classified tool call may run (ARCHITECTURE section 5.2).

The model has no policy tool. Policy lives in user-owned configuration plus compiled defaults.
At runtime the engine can only escalate: taint, session mode, and per-tool overrides raise the
requirement, nothing lowers it. A policy file that cannot be understood is an error, never a
silent fall-back to defaults.
"""

from __future__ import annotations

import logging
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opsagent.domain import ApprovalMode, PolicyDecision, Principal, RiskClass, SessionMode
from opsagent.tools.spec import TOOL_NAME

log = logging.getLogger(__name__)

_POLICY_KEYS = {
    "r0_auto",
    "r1_auto",
    "r2_requires_approval",
    "r3_requires_approval",
    "r4_requires_approval",
}
_OVERRIDE_KEYS = {"decision", "deny", "require_approval"}


class PolicyError(ValueError):
    """policy.toml is invalid. The app must not start on a policy it cannot read."""


@dataclass(frozen=True)
class ToolPolicy:
    """Per-tool override. Overrides only tighten: REQUIRE_APPROVAL or DENY."""

    tool: str
    decision: PolicyDecision


@dataclass
class PolicyConfig:
    """User-owned policy. R0/R1 may auto-run; R2 is the user's call; R3 and R4 always ask."""

    r0_auto: bool = True
    r1_auto: bool = True  # drafts, tentative events
    r2_requires_approval: bool = True  # False: R2 runs without asking (the user's choice)
    r3_requires_approval: bool = True  # cannot be disabled (ARCHITECTURE 5.1)
    r4_requires_approval: bool = True  # cannot be disabled
    overrides: list[ToolPolicy] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.r3_requires_approval or not self.r4_requires_approval:
            raise PolicyError("R3 and R4 always require approval; they cannot be relaxed")

    @classmethod
    def from_toml(cls, text: str) -> PolicyConfig:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise PolicyError(f"policy.toml is not valid TOML: {exc}") from exc
        unknown_top = set(data) - {"policy", "tools"}
        if unknown_top:
            raise PolicyError(f"unknown policy sections: {sorted(unknown_top)}")

        rules = data.get("policy", {})
        if not isinstance(rules, dict) or set(rules) - _POLICY_KEYS:
            extra = sorted(set(rules) - _POLICY_KEYS) if isinstance(rules, dict) else rules
            raise PolicyError(f"unknown keys in [policy]: {extra}")
        for key, value in rules.items():
            if not isinstance(value, bool):
                raise PolicyError(f"[policy] {key} must be true or false")

        overrides: list[ToolPolicy] = []
        tools = data.get("tools", {})
        if not isinstance(tools, dict):
            raise PolicyError("[tools] must be a table")
        _collect_overrides("", tools, overrides)
        return cls(**rules, overrides=overrides)

    def to_toml(self) -> str:
        lines = [
            "# User policy configuration (ARCHITECTURE 5.2). Edited by `ops policy ...`.",
            "[policy]",
            f"r0_auto = {str(self.r0_auto).lower()}",
            f"r1_auto = {str(self.r1_auto).lower()}",
            f"r2_requires_approval = {str(self.r2_requires_approval).lower()}",
            f"r3_requires_approval = {str(self.r3_requires_approval).lower()}",
            f"r4_requires_approval = {str(self.r4_requires_approval).lower()}",
            "",
        ]
        for override in sorted(self.overrides, key=lambda o: o.tool):
            lines += [f'[tools."{override.tool}"]', f'decision = "{override.decision.value}"', ""]
        return "\n".join(lines)

    def with_override(self, tool: str, decision: PolicyDecision) -> PolicyConfig:
        if not TOOL_NAME.match(tool):
            raise PolicyError(f"{tool!r} is not a valid tool name (like 'mail.send_message')")
        if decision is PolicyDecision.ALLOW:
            raise PolicyError("overrides can only tighten, not allow")
        kept = [o for o in self.overrides if o.tool != tool]
        return PolicyConfig(
            self.r0_auto,
            self.r1_auto,
            self.r2_requires_approval,
            self.r3_requires_approval,
            self.r4_requires_approval,
            [*kept, ToolPolicy(tool, decision)],
        )

    def without_override(self, tool: str) -> PolicyConfig:
        return PolicyConfig(
            self.r0_auto,
            self.r1_auto,
            self.r2_requires_approval,
            self.r3_requires_approval,
            self.r4_requires_approval,
            [o for o in self.overrides if o.tool != tool],
        )


def _collect_overrides(prefix: str, table: dict[str, Any], out: list[ToolPolicy]) -> None:
    """Walk `[tools.a.b]` tables (dotted keys nest in TOML) and collect leaf overrides."""
    for key, value in table.items():
        name = f"{prefix}.{key}" if prefix else key
        if not TOOL_NAME.match(name):
            raise PolicyError(f"[tools] entry {name!r} is not a valid tool name")
        if not isinstance(value, dict):
            raise PolicyError(f"[tools] entry {name!r} must be a table")
        if set(value) & _OVERRIDE_KEYS:
            out.append(ToolPolicy(tool=name, decision=_override_decision(name, value)))
        else:
            _collect_overrides(name, value, out)


def _override_decision(name: str, body: dict[str, Any]) -> PolicyDecision:
    if set(body) - _OVERRIDE_KEYS:
        raise PolicyError(f"unknown keys for tool {name!r}: {sorted(set(body) - _OVERRIDE_KEYS)}")
    if "decision" in body:
        try:
            decision = PolicyDecision(str(body["decision"]).upper())
        except ValueError as exc:
            raise PolicyError(f"tool {name!r}: unknown decision {body['decision']!r}") from exc
    elif body.get("deny") is True:
        decision = PolicyDecision.DENY
    elif body.get("require_approval") is True:
        decision = PolicyDecision.REQUIRE_APPROVAL
    else:
        raise PolicyError(f"tool {name!r}: override must set a decision")
    if decision is PolicyDecision.ALLOW:
        raise PolicyError(f"tool {name!r}: overrides can only tighten, not allow")
    return decision


def load_policy(path: Path) -> PolicyConfig:
    """Compiled defaults when there is no file. A file that is present must be valid."""
    if not path.is_file():
        return PolicyConfig()
    return PolicyConfig.from_toml(path.read_text(encoding="utf-8"))


def save_policy(path: Path, config: PolicyConfig) -> None:
    """Write atomically with owner-only permissions, so a crash never leaves half a policy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".policy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(config.to_toml())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class PolicyEngine:
    """Decides whether a classified tool call may run. Tightening only; the model cannot see it."""

    def __init__(self, config: PolicyConfig | None = None, mode: SessionMode | None = None) -> None:
        self._config = config or PolicyConfig()
        self._overrides = {o.tool: o.decision for o in self._config.overrides}
        self._mode = mode or SessionMode()

    def decide(
        self,
        *,
        tool: str,
        risk: RiskClass,
        principal: Principal,
        tainted: bool = False,
    ) -> PolicyDecision:
        """Return ALLOW | REQUIRE_APPROVAL | DENY.

        Order, strictest first: a DENY override; plan mode (read-only); the risk class default;
        taint (a tainted run cannot auto-run anything above R0); a REQUIRE_APPROVAL override.
        `principal` does not change the answer today: every principal is treated as the agent.
        """
        override = self._overrides.get(tool)
        if override is PolicyDecision.DENY:
            return PolicyDecision.DENY
        if self._mode.current is ApprovalMode.PLAN and risk is not RiskClass.R0:
            return PolicyDecision.DENY

        base = self._base_decision(risk)
        if base is PolicyDecision.ALLOW and tainted and risk.rank >= 1:
            log.debug("policy: taint escalates %s to REQUIRE_APPROVAL", tool)
            return PolicyDecision.REQUIRE_APPROVAL
        if base is PolicyDecision.ALLOW and override is PolicyDecision.REQUIRE_APPROVAL:
            return PolicyDecision.REQUIRE_APPROVAL
        return base

    def _base_decision(self, risk: RiskClass) -> PolicyDecision:
        cfg = self._config
        auto = {
            RiskClass.R0: cfg.r0_auto,
            RiskClass.R1: cfg.r1_auto,
            RiskClass.R2: not cfg.r2_requires_approval,
            RiskClass.R3: False,  # R3 and R4 always ask; PolicyConfig refuses to relax them
            RiskClass.R4: False,
        }[risk]
        return PolicyDecision.ALLOW if auto else PolicyDecision.REQUIRE_APPROVAL
