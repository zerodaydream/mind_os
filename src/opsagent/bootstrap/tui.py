"""Launch the terminal UI."""

from __future__ import annotations

import os
from pathlib import Path

from opsagent import __version__
from opsagent.agents.fake import FakeRuntime
from opsagent.bootstrap.container import build_container
from opsagent.domain import ModelRole, ReasoningEffort
from opsagent.infra import Settings, configure_logging
from opsagent.presentation import OpsApp, SessionInfo


def _plain_terminal() -> bool:
    """NO_COLOR, TERM=dumb, or OPS_ASCII=1 turns off color, motion, and symbols."""
    return (
        bool(os.environ.get("NO_COLOR"))
        or os.environ.get("TERM") == "dumb"
        or (os.environ.get("OPS_ASCII") == "1")
    )


def _session_info(
    settings: Settings, container_llm_levels: tuple[ReasoningEffort, ...]
) -> SessionInfo:
    models = tuple(dict.fromkeys(settings.models().values()))  # orchestrator first, no repeats
    return SessionInfo(
        version=__version__,
        cwd=str(Path.cwd()).replace(str(Path.home()), "~", 1),
        models=models,
        effort=settings.reasoning_for(ModelRole.ORCHESTRATOR),
        effort_levels=container_llm_levels or tuple(ReasoningEffort),
        plain=_plain_terminal(),
    )


def run_tui(settings: Settings, *, demo: bool = False) -> None:
    """Start the UI. `demo` replays scripted scenarios instead of calling a model."""
    configure_logging(settings.log_dir, settings.log_level)
    container = build_container(settings, runtime=FakeRuntime() if demo else None)
    try:
        levels = container.llm.capabilities(ModelRole.ORCHESTRATOR).effort_levels
    except Exception:  # no model or key configured yet: offer every level
        levels = ()
    info = _session_info(settings, levels)
    OpsApp(container.app, info).run()
