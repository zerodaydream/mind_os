from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from opsagent.bootstrap import StartupError
from opsagent.bootstrap.tui import _preflight
from opsagent.domain import ModelRole, ReasoningEffort, bind
from opsagent.infra import (
    ConfigError,
    Settings,
    SettingsCredentialProvider,
    configure_logging,
    get_logger,
    load_settings,
    new_id,
)


def test_ids_sort_by_creation_time_and_are_unique() -> None:
    ids = [new_id("run") for _ in range(200)]
    assert len(set(ids)) == 200
    assert all(i.startswith("run_") and len(i) == 4 + 26 for i in ids)


def test_settings_defaults_and_role_fallback() -> None:
    s = Settings(_env_file=None, model_subagent="m-sub")
    assert s.reasoning_for(ModelRole.ORCHESTRATOR) == ReasoningEffort.HIGH
    assert s.model_for(ModelRole.UTILITY) == "m-sub"
    with pytest.raises(ConfigError, match="OPS_MODEL_ORCHESTRATOR"):
        s.model_for(ModelRole.ORCHESTRATOR)
    assert s.models() == {ModelRole.SUBAGENT: "m-sub", ModelRole.UTILITY: "m-sub"}


def test_env_file_is_read_without_touching_process_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPS_MODEL_SUBAGENT", "OPS_REASONING_SUBAGENT"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "OPS_MODEL_SUBAGENT=from-file\nOPS_REASONING_SUBAGENT=medium\n"
        "GEMINI_API_KEY=abc123\nUNRELATED_LEGACY=1\n"
    )
    s = load_settings(env)
    assert s.model_subagent == "from-file"
    assert s.reasoning_subagent == ReasoningEffort.MEDIUM
    assert SettingsCredentialProvider(s).get("gemini_api_key") == "abc123"
    assert "OPS_MODEL_SUBAGENT" not in os.environ


def test_invalid_values_rejected() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, log_level="loud")
    with pytest.raises(ValueError):
        Settings(_env_file=None, db_pool_max=0)


def test_logs_are_json_with_correlation_and_redaction(tmp_path: Path) -> None:
    log_file = configure_logging(tmp_path, "INFO")
    with bind(run_id="run_1"):
        get_logger("test").info("called", api_key="supersecret", note="token hf_" + "a" * 30)
    lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    entry = lines[-1]
    assert entry["event"] == "called"
    assert entry["run_id"] == "run_1"
    assert entry["level"] == "info"
    assert "supersecret" not in json.dumps(entry)
    assert "hf_" + "a" * 30 not in json.dumps(entry)


def test_startup_preflight_names_the_missing_model() -> None:
    with pytest.raises(StartupError, match="OPS_MODEL_ORCHESTRATOR"):
        _preflight(Settings(_env_file=None, model_orchestrator=None, model_subagent="m"))
