"""Wipe local ingest store + GDrive attachment folders."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete, text, update

from cli import term
from backend.common.config_loader.settings import get_settings
from backend.common.database import lifecycle
from backend.common.database.models import Commitment, OutboxEvent
from backend.services.integrations.core.gdrive.drive_store import (
    gdrive_ingest_configured,
    trash_gdrive_ingest_roots,
)
from backend.worker.bootstrap import bootstrap


def clear_vault_dir(vault_path: str) -> int:
    root = Path(vault_path).expanduser().resolve()
    if not root.is_dir():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path.name != ".gitkeep":
            path.unlink()
            removed += 1
    return removed


async def run_cleanup() -> int:
    bootstrap()
    settings = get_settings()
    if lifecycle._session_factory is None:
        term.error("database not initialized")
        return 1

    term.banner()
    term.step("Cleanup", "artifacts · chunks · vault · GDrive ingest folders")

    gdrive_roots = 0
    if gdrive_ingest_configured():
        try:
            gdrive_roots = await trash_gdrive_ingest_roots()
            term.step("GDrive", f"trashed {gdrive_roots} ingest folder(s)", ok=True)
        except Exception as exc:
            term.step("GDrive", str(exc), ok=False)
            term.note("Continuing with local wipe.")
    else:
        term.note("GDrive not configured; skipped Drive folder trash")

    term.note("Stop ingest if cleanup hangs (idle DB transactions hold locks).")
    term.step("Database", "TRUNCATE artifacts CASCADE (chunks, embeddings)…")
    async with lifecycle._session_factory() as session:
        art_count = await session.scalar(text("SELECT count(*) FROM artifacts")) or 0
        await session.execute(
            update(Commitment)
            .where(Commitment.source_chunk_id.is_not(None))
            .values(source_chunk_id=None)
        )
        await session.execute(
            text("UPDATE edges SET evidence_chunk = NULL WHERE evidence_chunk IS NOT NULL")
        )
        outbox = await session.execute(
            delete(OutboxEvent).where(OutboxEvent.event_type == "artifact.process")
        )
        # shortcut: CASCADE also truncates chunk-linked rows on this schema; upgrade to selective delete if needed
        await session.execute(text("TRUNCATE artifacts RESTART IDENTITY CASCADE"))
        await session.commit()
        n_arts = int(art_count)
        n_outbox = int(outbox.rowcount or 0)

    vault_files = clear_vault_dir(settings.mindos_vault_path)
    term.step(
        "Database",
        f"artifacts {n_arts} · outbox {n_outbox} · vault files {vault_files}",
        ok=True,
    )
    return 0