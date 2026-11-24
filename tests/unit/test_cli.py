"""Tests for the `mindos` CLI commands."""

from __future__ import annotations

from pathlib import Path

import keyring
import pytest
from typer.testing import CliRunner

from mindos.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "mindos " in result.stdout


def test_cli_policy_show_default(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["policy", "show"])
    assert result.exit_code == 0
    assert "compiled default policy" in result.stdout
    assert "R0 auto-allow: True" in result.stdout


@pytest.fixture
def audited(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Replace the database write with a recorder, so unit tests need no services."""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        "mindos.cli._record_policy_change",
        lambda _s, tool, change, detail: calls.append((tool, change, detail)),
    )
    return calls


def test_cli_policy_tighten_and_reset(monkeypatch, tmp_path: Path, audited: list) -> None:  # type: ignore[no-untyped-def,type-arg]
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))
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

    assert audited == [
        ("mail.send", "tighten", "REQUIRE_APPROVAL"),
        ("dangerous.drop", "tighten", "DENY"),
        ("mail.send", "reset", "removed override"),
    ]


def test_a_policy_change_that_cannot_be_audited_is_not_made(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))

    async def database_down(*_a: object, **_k: object) -> None:
        raise ConnectionError("database is down")

    monkeypatch.setattr("mindos.cli.open_pool", database_down)
    result = runner.invoke(app, ["policy", "tighten", "mail.send_message"])
    assert result.exit_code == 1
    assert "could not be audited" in result.stdout
    assert not (tmp_path / "policy.toml").exists()


def test_an_invalid_policy_file_is_an_error_not_a_silent_default(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "policy.toml").write_text('[policy]\nr0_auto = "yes"\n')
    for args in (["policy", "show"], ["policy", "tighten", "mail.send_message"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2 and "not valid" in result.stdout


def test_tighten_rejects_a_tool_name_that_would_corrupt_the_file(
    monkeypatch,
    tmp_path: Path,
    audited: list,  # type: ignore[no-untyped-def,type-arg]
) -> None:
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["policy", "tighten", 'x"]\n[policy]\nr0_auto=false'])
    assert result.exit_code == 2
    assert audited == [] and not (tmp_path / "policy.toml").exists()


def test_resetting_an_override_that_does_not_exist_changes_and_audits_nothing(
    monkeypatch,
    tmp_path: Path,
    audited: list,  # type: ignore[no-untyped-def,type-arg]
) -> None:
    monkeypatch.setenv("MINDOS_CONFIG_DIR", str(tmp_path))
    result = runner.invoke(app, ["policy", "reset", "mail.send_message"])
    assert result.exit_code == 0 and "No override found" in result.stdout
    assert audited == []


def test_auth_requires_a_capability_and_rejects_unknown_ones() -> None:
    bare = runner.invoke(app, ["auth", "google"])
    assert bare.exit_code == 2 and "mail-send" in bare.stdout
    unknown = runner.invoke(app, ["auth", "google", "-c", "mail-everything"])
    assert unknown.exit_code == 2 and "unknown google capability" in unknown.stdout


def test_auth_gemini_stores_the_key_in_the_keychain_and_never_echoes_it() -> None:
    result = runner.invoke(app, ["auth", "gemini"], input="AIza-secret-key\n")
    assert result.exit_code == 0 and "Stored" in result.stdout
    assert "AIza-secret-key" not in result.stdout
    assert keyring.get_password("mindos", "gemini_api_key") is None  # the test keyring is null
