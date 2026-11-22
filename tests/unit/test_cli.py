"""Tests for the `ops` CLI commands."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from opsagent.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "ops " in result.stdout


def test_cli_policy_show_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPS_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["policy", "show"])
    assert result.exit_code == 0
    assert "compiled default policy" in result.stdout
    assert "R0 auto-allow: True" in result.stdout


def test_cli_policy_tighten_and_reset(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPS_CONFIG_DIR", str(tmp_path))
    policy_file = tmp_path / "policy.toml"
    assert not policy_file.exists()

    # Tighten tool to require_approval
    result = runner.invoke(app, ["policy", "tighten", "mail.send"])
    assert result.exit_code == 0
    assert "Updated policy for 'mail.send' -> REQUIRE_APPROVAL" in result.stdout
    assert policy_file.is_file()

    # Show policy reflects tightening
    result = runner.invoke(app, ["policy", "show"])
    assert result.exit_code == 0
    assert "mail.send: REQUIRE_APPROVAL" in result.stdout

    # Tighten tool to deny
    result = runner.invoke(app, ["policy", "tighten", "dangerous.drop", "--deny"])
    assert result.exit_code == 0
    assert "dangerous.drop' -> DENY" in result.stdout

    # Reset tool
    result = runner.invoke(app, ["policy", "reset", "mail.send"])
    assert result.exit_code == 0
    assert "Reset policy for 'mail.send'" in result.stdout

    result = runner.invoke(app, ["policy", "show"])
    assert "mail.send" not in result.stdout
    assert "dangerous.drop: DENY" in result.stdout
