"""The composition root must not start on a policy it cannot read, and must wire plan mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mindos.bootstrap.container import build_container
from mindos.infra import Settings
from mindos.tools import PolicyError


def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, config_dir=tmp_path, model_orchestrator="m", model_subagent="m")


def test_an_unreadable_policy_file_stops_startup_instead_of_falling_back_to_defaults(
    tmp_path: Path,
) -> None:
    (tmp_path / "policy.toml").write_text("[policy]\nr0_auto = 3\n")
    pool: Any = object()  # nothing is touched before the policy is read
    with pytest.raises(PolicyError):
        build_container(settings(tmp_path), pool=pool, checkpointer=object())  # type: ignore[arg-type]


def test_without_a_database_no_approval_machinery_is_wired(tmp_path: Path) -> None:
    container = build_container(settings(tmp_path))
    assert container.broker is None and container.executions is None
