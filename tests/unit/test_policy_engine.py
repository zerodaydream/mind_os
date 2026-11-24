"""Tests for PolicyEngine: risk classification, taint escalation, and configuration."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from opsagent.domain import ApprovalMode, PolicyDecision, Principal, RiskClass, SessionMode
from opsagent.tools.policy import (
    PolicyConfig,
    PolicyEngine,
    PolicyError,
    ToolPolicy,
    load_policy,
    save_policy,
)
from tests.support.policies import ReadOnlyPolicy


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
    assert (
        policy.decide(
            tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT, tainted=False
        )
        == PolicyDecision.ALLOW
    )
    # Tainted: R1 escalates to REQUIRE_APPROVAL
    assert (
        policy.decide(tool="test.draft", risk=RiskClass.R1, principal=Principal.AGENT, tainted=True)
        == PolicyDecision.REQUIRE_APPROVAL
    )


def test_taint_does_not_affect_r0() -> None:
    policy = PolicyEngine()
    # R0 (read-only) remains ALLOW even in tainted run
    assert (
        policy.decide(tool="test.read", risk=RiskClass.R0, principal=Principal.AGENT, tainted=True)
        == PolicyDecision.ALLOW
    )


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
    assert '[tools."mail_send"]' in serialized
    assert 'decision = "REQUIRE_APPROVAL"' in serialized

    reloaded = PolicyConfig.from_toml(serialized)
    assert reloaded.r1_auto == cfg.r1_auto
    assert set(reloaded.overrides) == set(cfg.overrides)


# ---- review additions: modes, strict parsing, atomic save ------------------------------------
def test_plan_mode_denies_everything_above_r0_and_leaves_reads_alone() -> None:
    mode = SessionMode(ApprovalMode.PLAN)
    policy = PolicyEngine(PolicyConfig(), mode)
    agent = Principal.AGENT
    assert policy.decide(tool="t", risk=RiskClass.R0, principal=agent) == PolicyDecision.ALLOW
    for risk in (RiskClass.R1, RiskClass.R2, RiskClass.R3, RiskClass.R4):
        assert policy.decide(tool="t", risk=risk, principal=agent) == PolicyDecision.DENY
    mode.set(ApprovalMode.DEFAULT)  # the same engine sees the change: one shared holder
    assert policy.decide(tool="t", risk=RiskClass.R1, principal=agent) == PolicyDecision.ALLOW


def test_r2_can_be_relaxed_by_the_user_but_r3_and_r4_cannot() -> None:
    relaxed = PolicyEngine(PolicyConfig(r2_requires_approval=False))
    assert relaxed.decide(tool="t", risk=RiskClass.R2, principal=Principal.AGENT) == (
        PolicyDecision.ALLOW
    )
    # taint still escalates a relaxed R2
    assert (
        relaxed.decide(tool="t", risk=RiskClass.R2, principal=Principal.AGENT, tainted=True)
        == PolicyDecision.REQUIRE_APPROVAL
    )
    with pytest.raises(PolicyError, match="R3 and R4"):
        PolicyConfig(r3_requires_approval=False)
    with pytest.raises(PolicyError, match="R3 and R4"):
        PolicyConfig.from_toml("[policy]\nr4_requires_approval = false\n")


@pytest.mark.parametrize(
    "text",
    [
        "not toml = = =",
        "[polcy]\nr0_auto = true\n",  # typo in a section name must not be ignored
        "[policy]\nr0_autoo = true\n",  # typo in a key
        '[policy]\nr0_auto = "yes"\n',  # wrong type
        '[tools."mail.send_message"]\ndecision = "ALOW"\n',  # unknown decision
        '[tools."mail.send_message"]\ndecision = "ALLOW"\n',  # overrides cannot loosen
        '[tools."mail.send_message"]\nfoo = 1\ndecision = "DENY"\n',
        '[tools."Mail Send"]\ndecision = "DENY"\n',  # not a tool name
    ],
)
def test_invalid_policy_is_an_error_never_silently_ignored(text: str) -> None:
    with pytest.raises(PolicyError):
        PolicyConfig.from_toml(text)


def test_dotted_tool_tables_and_legacy_booleans_parse() -> None:
    cfg = PolicyConfig.from_toml(
        "[tools.mail.send_message]\ndeny = true\n[tools.files.edit]\nrequire_approval = true\n"
    )
    assert {o.tool: o.decision for o in cfg.overrides} == {
        "mail.send_message": PolicyDecision.DENY,
        "files.edit": PolicyDecision.REQUIRE_APPROVAL,
    }


def test_with_override_validates_the_name_that_is_written_into_toml() -> None:
    cfg = PolicyConfig()
    with pytest.raises(PolicyError):
        cfg.with_override('x"]\n[policy]\nr0_auto = false', PolicyDecision.DENY)
    with pytest.raises(PolicyError):
        cfg.with_override("mail.send_message", PolicyDecision.ALLOW)
    updated = cfg.with_override("mail.send_message", PolicyDecision.DENY)
    assert updated.without_override("mail.send_message").overrides == []
    assert cfg.overrides == []  # the original is untouched


def test_load_policy_missing_file_is_defaults_but_a_bad_file_raises(tmp_path: Path) -> None:
    assert load_policy(tmp_path / "policy.toml") == PolicyConfig()
    bad = tmp_path / "policy.toml"
    bad.write_text("[policy]\nr0_auto = 3\n")
    with pytest.raises(PolicyError):
        load_policy(bad)


def test_save_policy_is_atomic_private_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "policy.toml"
    cfg = PolicyConfig().with_override("mail.send_message", PolicyDecision.DENY)
    save_policy(target, cfg)
    assert load_policy(target).overrides == cfg.overrides
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert [p.name for p in target.parent.iterdir()] == ["policy.toml"]  # no temp file left
