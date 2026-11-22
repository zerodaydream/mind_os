"""Tests for PolicyEngine: risk classification, taint escalation, and configuration."""

from __future__ import annotations

from opsagent.domain import PolicyDecision, Principal, RiskClass
from opsagent.tools.policy import PolicyConfig, PolicyEngine, ReadOnlyPolicy, ToolPolicy


def test_default_policy_decisions() -> None:
    policy = PolicyEngine()
    # R0 and R1 are auto-allowed by default
    assert policy.decide(tool="test.read", risk=RiskClass.R0, principal=Principal.AGENT) == (
        PolicyDecision.ALLOW
    )
    assert policy.decide(tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT) == (
        PolicyDecision.ALLOW
    )
    # R2, R3, R4 require approval by default
    assert policy.decide(tool="test.edit", risk=RiskClass.R2, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )
    assert policy.decide(tool="test.send", risk=RiskClass.R3, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )
    assert policy.decide(tool="test.bulk", risk=RiskClass.R4, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )


def test_taint_escalation_raises_r1_to_require_approval() -> None:
    policy = PolicyEngine()
    # Untainted: R1 is ALLOW
    assert policy.decide(
        tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT, tainted=False
    ) == PolicyDecision.ALLOW
    # Tainted: R1 escalates to REQUIRE_APPROVAL
    assert policy.decide(
        tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT, tainted=True
    ) == PolicyDecision.REQUIRE_APPROVAL


def test_taint_does_not_affect_r0() -> None:
    policy = PolicyEngine()
    # R0 (read-only) remains ALLOW even in tainted run
    assert policy.decide(
        tool="test.read", risk=RiskClass.R0, principal=Principal.AGENT, tainted=True
    ) == PolicyDecision.ALLOW


def test_custom_config_tightens_r1() -> None:
    cfg = PolicyConfig(r1_auto=False)
    policy = PolicyEngine(cfg)
    assert policy.decide(tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )


def test_per_tool_override_deny() -> None:
    cfg = PolicyConfig(overrides=[ToolPolicy(tool="dangerous.read", decision=PolicyDecision.DENY)])
    policy = PolicyEngine(cfg)
    # Even though R0 is normally allowed, this tool is denied
    assert policy.decide(tool="dangerous.read", risk=RiskClass.R0, principal=Principal.AGENT) == (
        PolicyDecision.DENY
    )


def test_per_tool_override_can_tighten_to_require_approval() -> None:
    cfg = PolicyConfig(
        overrides=[ToolPolicy(tool="sensitive.read", decision=PolicyDecision.REQUIRE_APPROVAL)]
    )
    policy = PolicyEngine(cfg)
    # R0 tool tightened to REQUIRE_APPROVAL
    assert policy.decide(tool="sensitive.read", risk=RiskClass.R0, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )


def test_per_tool_override_cannot_loosen_r3() -> None:
    # Attempting to override an R3 tool to ALLOW must NOT lower the decision
    cfg = PolicyConfig(overrides=[ToolPolicy(tool="mail.send", decision=PolicyDecision.ALLOW)])
    policy = PolicyEngine(cfg)
    # PolicyEngine base rule for R3 is REQUIRE_APPROVAL, override cannot loosen it
    assert policy.decide(tool="mail.send", risk=RiskClass.R3, principal=Principal.AGENT) == (
        PolicyDecision.REQUIRE_APPROVAL
    )


def test_readonly_policy_stub() -> None:
    ro = ReadOnlyPolicy()
    assert ro.decide(tool="x", risk=RiskClass.R0, principal=Principal.AGENT) == PolicyDecision.ALLOW
    assert ro.decide(tool="x", risk=RiskClass.R1, principal=Principal.AGENT) == PolicyDecision.DENY
    assert ro.decide(tool="x", risk=RiskClass.R3, principal=Principal.AGENT) == PolicyDecision.DENY


def test_policy_config_toml_roundtrip() -> None:
    toml_text = """
[policy]
r0_auto = true
r1_auto = false
r2_requires_approval = true
r3_requires_approval = true
r4_requires_approval = true

[tools.mail_send]
decision = "require_approval"

[tools.dangerous_delete]
decision = "deny"
"""
    cfg = PolicyConfig.from_toml(toml_text)
    assert cfg.r0_auto is True
    assert cfg.r1_auto is False
    assert len(cfg.overrides) == 2
    assert cfg.overrides[0] == ToolPolicy(
        tool="mail_send", decision=PolicyDecision.REQUIRE_APPROVAL
    )
    assert cfg.overrides[1] == ToolPolicy(tool="dangerous_delete", decision=PolicyDecision.DENY)

    serialized = cfg.to_toml()
    assert "r1_auto = false" in serialized
    assert "[tools.mail_send]" in serialized
    assert 'decision = "REQUIRE_APPROVAL"' in serialized

    reloaded = PolicyConfig.from_toml(serialized)
    assert reloaded.r1_auto == cfg.r1_auto
    assert reloaded.overrides == cfg.overrides
