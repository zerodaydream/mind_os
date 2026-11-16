import asyncio
import logging
from collections.abc import Callable
from sqlalchemy import func, select

from backend.common.config_loader.settings import get_settings
from backend.common.database import lifecycle
from backend.common.database.models import OutboxEvent
from backend.common.database.outbox import claim_next, mark_done, mark_failed
from backend.worker.handlers.artifact_process import handle_artifact_process
from backend.worker.handlers.gmail_sync import handle_gmail_sync
from backend.worker.outbox_say import bind_outbox_say, reset_outbox_say

logger = logging.getLogger(__name__)

_HANDLERS = {
    "artifact.process": handle_artifact_process,
    "integrations.gmail.sync": handle_gmail_sync,
}


async def _pending_outbox_count(session) -> int:
    result = await session.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.status == "pending")
    )
    return int(result.scalar_one())


async def _process_one(*, on_step: Callable[[str], None] | None = None) -> bool:
    if lifecycle._session_factory is None:
        return False
    async with lifecycle._session_factory() as session:
        event = await claim_next(session)
        if event is None:
            return False
        event_id = event.id
        handler = _HANDLERS.get(event.event_type)
        token = bind_outbox_say(on_step)
        try:
            if on_step is not None:
                pending = await _pending_outbox_count(session)
                line = _event_step_line(event)
                on_step(f"{line} ({pending} pending)")
            if handler is None:
                raise ValueError(f"unknown event_type {event.event_type}")
            await handler(session, event.payload)
            await mark_done(session, event_id)
            await session.commit()
            if on_step is not None:
                on_step(f"{_event_step_line(event)} · ok")
        except Exception as exc:
            await session.rollback()
            if on_step is not None:
                on_step(f"{_event_step_line(event)} · failed: {exc}")
            async with lifecycle._session_factory() as s2:
                ev = await s2.get(OutboxEvent, event_id)
                if ev:
                    await mark_failed(s2, ev, error=str(exc))
                    await s2.commit()
            logger.exception("outbox handler failed: %s", exc)
        finally:
            reset_outbox_say(token)
    return True


def _event_step_line(event: OutboxEvent) -> str:
    if event.event_type == "artifact.process":
        raw = str(event.payload.get("artifact_id") or "")
        return f"artifact.process {raw[:8]}…" if raw else "artifact.process"
    return event.event_type


async def drain_outbox(
    *,
    max_events: int = 500,
    on_step: Callable[[str], None] | None = None,
) -> int:
    if on_step is not None:
        async with lifecycle._session_factory() as session:
            pending = await _pending_outbox_count(session)
        if pending == 0:
            on_step("queue empty")
            return 0
        on_step(f"{pending} pending event(s)")
    processed = 0
    for _ in range(max_events):
        if not await _process_one(on_step=on_step):
            break
        processed += 1
    return processed


async def run_forever() -> None:
    interval = get_settings().worker_poll_sec
    while True:
        if not await _process_one():
            await asyncio.sleep(interval)