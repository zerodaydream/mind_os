"""``mindos status``: connectivity, counts, and feature flags."""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select, text

from cli import term
from cli.env import bootstrap_env
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings, reload_settings
from backend.common.langsmith_setup import tracing_active
from backend.common.database import lifecycle
from backend.common.database.models import IntegrationAccount
from backend.services.ingest.core.manual import ensure_default_user
from backend.worker.bootstrap import bootstrap


async def _status_async() -> int:
    bootstrap_env()
    reload_settings()
    bootstrap()
    settings = get_settings()
    cfg = get_config()
    term.banner()
    term.step("MINDOS_HOME", os.environ.get("MINDOS_HOME", "(cwd)"))
    term.step("Database", settings.database_url.split("@")[-1] if "@" in settings.database_url else "…")
    if lifecycle._session_factory is None:
        term.step("Postgres", "not connected", ok=False)
        return 1
    async with lifecycle._session_factory() as session:
        await session.execute(text("SELECT 1"))
        term.step("Postgres", "ok", ok=True)
        user = await ensure_default_user(session, settings)
        row = await session.execute(
            select(IntegrationAccount).where(
                IntegrationAccount.user_id == user.id,
                IntegrationAccount.provider == "google",
            )
        )
        acct = row.scalar_one_or_none()
        if acct and acct.status == "active":
            term.step("Gmail", "connected (OAuth stored)", ok=True)
        else:
            term.step(
                "Gmail",
                "not connected — open /v1/integrations/google/start in a browser once",
                ok=False,
            )
        n = await session.execute(
            text(
                "SELECT count(*) FROM artifacts WHERE user_id = :uid AND source = 'gmail'"
            ),
            {"uid": user.id},
        )
        g = n.scalar_one()
        n2 = await session.execute(
            text(
                "SELECT count(*) FROM artifacts WHERE user_id = :uid AND source = 'manual'"
            ),
            {"uid": user.id},
        )
        m = n2.scalar_one()
        term.step("Artifacts", f"gmail {g} · manual {m}")
        try:
            cs = await session.execute(
                text("SELECT count(*) FROM chat_sessions WHERE user_id = :uid"),
                {"uid": user.id},
            )
            term.step("Chat sessions", str(cs.scalar_one()))
        except Exception:
            term.step(
                "Chat sessions",
                "table missing — uv run alembic upgrade head (host → localhost:5433)",
                ok=False,
            )
        term.step(
            "Sync interval",
            f"{settings.mindos_sync_interval_days:g} day(s) (MINDOS_SYNC_INTERVAL_DAYS)",
        )
        term.step("integrations", "on" if cfg.is_enabled("integrations") else "off")
        if settings.langsmith_tracing:
            if settings.langsmith_agent_id:
                dest = f"{settings.langsmith_agent_id} / {settings.langsmith_agent_environment}"
                hint = "LangSmith UI → pick that agent → env Local → Tracing"
            else:
                dest = settings.langsmith_project or "mind-os"
                hint = f"LangSmith UI → project {dest} → Tracing"
            term.step(
                "LangSmith",
                f"{dest} (active={tracing_active()}) — {hint}",
                ok=tracing_active() and bool(settings.langsmith_api_key),
            )
        term.step(
            "Docker logs",
            "docker compose --profile lean logs -f api worker",
        )
    return 0


def cmd_status() -> int:
    """Print health summary; return 0 on success, 1 if Postgres is unreachable."""
    from cli.async_util import run_async

    return run_async(_status_async())
