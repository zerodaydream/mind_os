"""Launch the terminal UI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psycopg

from opsagent import __version__
from opsagent.agents.fake import FakeRuntime
from opsagent.application.reconciliation import run_reconciliation
from opsagent.bootstrap.container import Container, build_container
from opsagent.domain import ModelRole, ReasoningEffort
from opsagent.infra import ConfigError, Settings, configure_logging
from opsagent.persistence import (
    ApprovalRepository,
    open_checkpointer,
    open_pool,
    schema_is_current,
)
from opsagent.presentation import OpsApp, SessionInfo


class StartupError(RuntimeError):
    """The app cannot start. The message says what to run or set."""


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
    if demo:
        container = build_container(settings, runtime=FakeRuntime())
        OpsApp(container.app, _info(settings, container)).run()
        return
    _preflight(settings)
    asyncio.run(_run_live(settings))


def _info(settings: Settings, container: Container) -> SessionInfo:
    try:
        levels = container.llm.capabilities(ModelRole.ORCHESTRATOR).effort_levels
    except Exception:  # no model or key configured yet: offer every level
        levels = ()
    return _session_info(settings, levels)


def _preflight(settings: Settings) -> None:
    """Fail early, with the command that fixes it, before the screen is taken over."""
    try:
        for role in (ModelRole.ORCHESTRATOR, ModelRole.SUBAGENT):
            settings.model_for(role)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc
    try:
        current = schema_is_current(settings.database_url)
    except Exception as exc:
        raise StartupError(
            "Cannot reach the database. Run `docker compose up -d db`, or set OPS_DATABASE_URL."
        ) from exc
    if not current:
        raise StartupError("The database schema is out of date. Run `ops db migrate`.")


async def _run_live(settings: Settings) -> None:
    try:
        pool = await open_pool(settings.database_url, max_size=settings.db_pool_max)
    except psycopg.Error as exc:
        raise StartupError("Cannot connect to the database. Run `ops doctor`.") from exc
    try:
        async with open_checkpointer(settings.database_url) as saver:
            container = build_container(settings, pool=pool, checkpointer=saver)
            await run_reconciliation(ApprovalRepository(pool))
            try:
                await OpsApp(container.app, _info(settings, container)).run_async()
            finally:
                await container.app.aclose()
    finally:
        await pool.close()
