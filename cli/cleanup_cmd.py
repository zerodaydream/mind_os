"""Wipe local ingest store + GDrive attachment folders."""

from __future__ import annotations

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


async def run_cleanup() -> int:
    bootstrap()
    settings = get_settings()
    if lifecycle._session_factory is None:
        term.error("database not initialized")
        return 1

    term.banner()
    term.step("Cleanup", "artifacts · chunks · GDrive ingest folders")

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

    term.step(
        "Database",
        f"artifacts {n_arts} · outbox {n_outbox}",
        ok=True,
    )
    return 0