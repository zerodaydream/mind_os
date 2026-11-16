"""Backfill Gmail into MindOS (all matching mail, paginated)."""

from __future__ import annotations

import time

from cli import term
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.common.database import lifecycle
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.integrations.core.gmail.sync import (
    DEFAULT_GMAIL_FULL_INGEST_QUERY,
    sync_gmail_inbox,
)
from backend.worker.bootstrap import bootstrap
from cli.outbox import drain_outbox_cli

_GMAIL_ATTACHMENT_PROBE_QUERY = DEFAULT_GMAIL_FULL_INGEST_QUERY


async def run_gmail_full_ingest(*, process: bool, query: str | None, limit: int) -> int:
    """Ingest Gmail matching query; optionally drain the outbox."""
    if not get_config().is_enabled("integrations"):
        term.error("integrations disabled in backend/config.json")
        return 1
    list_query = (query or "").strip()
    if not list_query:
        list_query = _GMAIL_ATTACHMENT_PROBE_QUERY if limit == 1 else DEFAULT_GMAIL_FULL_INGEST_QUERY
    term.banner()
    term.step("Starting Gmail ingest", "local database")
    bootstrap()
    settings = get_settings()
    if lifecycle._session_factory is None:
        term.error("database not initialized")
        return 1

    exit_code = 0
    total_ingested = total_skipped = total_scanned = 0
    cap = f"limit {limit}" if limit > 0 else "no cap"
    term.step("Gmail", f"query: {list_query} · {cap}")
    if limit == 1:
        term.note("tqdm bars: one message + per-attachment extract/download")
    elif limit == 0:
        term.note("tqdm progress bar; commits each API page. Safe to interrupt and re-run.")

    started = time.perf_counter()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        try:
            page_token: str | None = None
            est = None
            while True:
                stats = await sync_gmail_inbox(
                    session,
                    user=user,
                    settings=settings,
                    max_messages=limit if limit > 0 else 0,
                    query=list_query,
                    page_token=page_token,
                    single_list_page=limit == 0,
                )
                total_ingested += int(stats.get("ingested") or 0)
                total_skipped += int(stats.get("skipped") or 0)
                total_scanned += int(stats.get("scanned") or 0)
                if est is None:
                    est = stats.get("result_size_estimate")
                page_token = stats.get("next_page_token")
                if limit != 0 or not page_token:
                    break
            await session.commit()
            est_bit = f" · ~{est} matched" if isinstance(est, int) and est >= 0 else ""
            elapsed = time.perf_counter() - started
            term.step(
                "Gmail",
                f"ingested {total_ingested} · skipped {total_skipped} · "
                f"scanned {total_scanned}{est_bit} · {elapsed:.1f}s",
                ok=total_ingested > 0 or total_skipped > 0,
            )
            if limit == 1 and total_ingested == 0 and total_skipped == 1:
                term.note("Message already in DB; use a different --query or delete artifact to re-test.")
        except ValueError as exc:
            term.step("Gmail", str(exc), ok=False)
            exit_code = 1
        except FileNotFoundError as exc:
            term.step(
                "Gmail",
                f"{exc} — fix SSL_CERT_FILE in ~/.zshrc or run: unset SSL_CERT_FILE",
                ok=False,
            )
            exit_code = 1
        except Exception as exc:
            term.step("Gmail", str(exc), ok=False)
            exit_code = 1

    if process and exit_code == 0:
        try:
            n = await drain_outbox_cli()
        except Exception:
            return 1
        term.step("Outbox", f"done · {n} event(s)", ok=True)