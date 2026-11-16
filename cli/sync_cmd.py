"""Gmail ingest and optional outbox processing for ``mindos sync``."""

from __future__ import annotations

from cli import term
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.common.database import lifecycle
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.integrations.core.gmail.sync import sync_gmail_inbox
from backend.worker.bootstrap import bootstrap
from cli.outbox import drain_outbox_cli


async def _sync_gmail() -> dict:
    settings = get_settings()
    if lifecycle._session_factory is None:
        raise RuntimeError("database not initialized")
    merged = {"ingested": 0, "skipped": 0, "scanned": 0, "query": "", "capped": False}
    page_token: str | None = None
    resume_after: str | None = None
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        for _ in range(20):
            stats = await sync_gmail_inbox(
                session,
                user=user,
                settings=settings,
                page_token=page_token,
                after_msg_id=resume_after,
            )
            for key in ("ingested", "skipped", "scanned"):
                merged[key] += int(stats.get(key) or 0)
            merged["query"] = stats.get("query") or merged["query"]
            merged["capped"] = bool(stats.get("capped"))
            page_token = stats.get("next_page_token")
            resume_after = stats.get("resume_after_msg_id")
            await session.commit()
            if not merged["capped"]:
                break
            if not page_token and not resume_after:
                break
        return merged


async def run_sync(*, gmail_only: bool, process: bool) -> int:
    """Run configured ingest; optionally drain the worker outbox. Exit code 0/1."""
    if not get_config().is_enabled("integrations"):
        term.error("integrations disabled in backend/config.json")
        return 1
    term.banner()
    term.step("Starting sync", "local database")
    bootstrap()
    exit_code = 0

    if gmail_only:
        term.step("Gmail", "inbox sync (full ingest rules, new mail since last ingest)")
        try:
            stats = await _sync_gmail()
            term.step(
                "Gmail ingest",
                f"ingested {stats.get('ingested', 0)} · "
                f"skipped {stats.get('skipped', 0)} · "
                f"scanned {stats.get('scanned', 0)}",
                ok=True,
            )
            query = stats.get("query")
            if query:
                term.note(f"Gmail query: {query}")
            if stats.get("capped"):
                term.note(
                    "More Gmail matches remain; raised cap with another page "
                    "(or run mindos gmail-ingest --limit 0 for full backfill)."
                )
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

    term.note("status · chat — or run bare `mindos` for interactive mode")
    return exit_code
