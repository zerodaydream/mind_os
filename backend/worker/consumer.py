import asyncio
import logging

from backend.common.config_loader.settings import get_settings
from backend.common.database import lifecycle
from backend.common.database.models import OutboxEvent
from backend.common.database.outbox import claim_next, mark_done, mark_failed
from backend.worker.handlers.artifact_process import handle_artifact_process
from backend.worker.handlers.gmail_sync import handle_gmail_sync

logger = logging.getLogger(__name__)

_HANDLERS = {
    "artifact.process": handle_artifact_process,
    "integrations.gmail.sync": handle_gmail_sync,
}


async def _process_one() -> bool:
    if lifecycle._session_factory is None:
        return False
    async with lifecycle._session_factory() as session:
        event = await claim_next(session)
        if event is None:
            return False
        event_id = event.id
        handler = _HANDLERS.get(event.event_type)
        try:
            if handler is None:
                raise ValueError(f"unknown event_type {event.event_type}")
            await handler(session, event.payload)
            await mark_done(session, event_id)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            async with lifecycle._session_factory() as s2:
                ev = await s2.get(OutboxEvent, event_id)
                if ev:
                    await mark_failed(s2, ev, error=str(exc))
                    await s2.commit()
            logger.exception("outbox handler failed: %s", exc)
    return True


async def drain_outbox(*, max_events: int = 500) -> int:
    processed = 0
    for _ in range(max_events):
        if not await _process_one():
            break
        processed += 1
    return processed


async def run_forever() -> None:
    interval = get_settings().worker_poll_sec
    while True:
        if not await _process_one():
            await asyncio.sleep(interval)
