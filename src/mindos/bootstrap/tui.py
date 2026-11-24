"""Launch the terminal UI."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import psycopg

from mindos import __version__
from mindos.agents.fake import FakeRuntime
from mindos.application.reconciliation import reconcile_storage
from mindos.bootstrap.container import Container, build_container
from mindos.domain import ModelRole, ReasoningEffort
from mindos.infra import ConfigError, Settings, configure_logging
from mindos.persistence import (
    open_checkpointer,
    open_pool,
    schema_is_current,
)
from mindos.presentation import MindOSApp, SessionInfo
from mindos.tools import PolicyError, load_policy


class StartupError(RuntimeError):
    """The app cannot start. The message says what to run or set."""


def _plain_terminal() -> bool:
    """NO_COLOR, TERM=dumb, or MINDOS_ASCII=1 turns off color, motion, and symbols."""
    return (
        bool(os.environ.get("NO_COLOR"))
        or os.environ.get("TERM") == "dumb"
        or (os.environ.get("MINDOS_ASCII") == "1")
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
        MindOSApp(container.app, _info(settings, container)).run()
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
            "Cannot reach the database. Run `docker compose up -d db`, or set MINDOS_DATABASE_URL."
        ) from exc
    if not current:
        raise StartupError("The database schema is out of date. Run `mindos db migrate`.")
    policy_file = settings.config_dir / "policy.toml"
    try:
        load_policy(policy_file)
    except PolicyError as exc:
        raise StartupError(
            f"{policy_file} is not valid: {exc}. Fix it, or remove it to use the defaults."
        ) from exc


async def _run_live(settings: Settings) -> None:
    try:
        pool = await open_pool(settings.database_url, max_size=settings.db_pool_max)
    except psycopg.Error as exc:
        raise StartupError("Cannot connect to the database. Run `mindos doctor`.") from exc
    try:
        async with open_checkpointer(settings.database_url) as saver:
            container = build_container(settings, pool=pool, checkpointer=saver)
            if container.broker and container.executions and container.audit:
                await reconcile_storage(
                    broker=container.broker,
                    executions=container.executions,
                    audit=container.audit,
                )
            try:
                await MindOSApp(container.app, _info(settings, container)).run_async()
            finally:
                await container.app.aclose()
                if container.auth is not None:
                    await container.auth.http.aclose()
                if container.tracing is not None:
                    container.tracing.flush()  # sent in the background; do not lose the last runs
    finally:
        await pool.close()
