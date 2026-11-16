"""Visible outbox drain for CLI commands."""

from __future__ import annotations

from cli import term
from backend.worker.consumer import drain_outbox


async def drain_outbox_cli(*, max_events: int = 500) -> int:
    try:
        return await drain_outbox(
            max_events=max_events,
            on_step=lambda detail: term.step("Outbox", detail),
        )
    except Exception as exc:
        term.step("Outbox", str(exc), ok=False)
        raise