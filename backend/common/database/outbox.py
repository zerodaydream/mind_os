from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import OutboxEvent


async def enqueue_artifact_process(session: AsyncSession, artifact_id: str) -> None:
    """One pending artifact.process job per artifact."""
    aid = str(artifact_id)
    exists = await session.execute(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.event_type == "artifact.process",
            OutboxEvent.status == "pending",
            OutboxEvent.payload["artifact_id"].as_string() == aid,
        )
        .limit(1)
    )
    if exists.scalar_one_or_none() is not None:
        return
    await enqueue(session, "artifact.process", {"artifact_id": aid})


async def enqueue(
    session: AsyncSession,
    event_type: str,
    payload: dict,
    *,
    available_at: datetime | None = None,
) -> OutboxEvent:
    """Enqueue an event to the outbox."""
    row = OutboxEvent(
        event_type=event_type,
        payload=payload,
        status="pending",
        available_at=available_at or datetime.now(timezone.utc),
    )
    session.add(row)
    await session.flush()
    return row


async def claim_next(session: AsyncSession) -> OutboxEvent | None:
    """Claim the next event from the outbox."""
    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.status == "pending",
            OutboxEvent.available_at <= datetime.now(timezone.utc),
        )
        .order_by(OutboxEvent.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        return None
    event.status = "processing"
    await session.flush()
    return event


async def mark_done(session: AsyncSession, event_id: int) -> None:
    """Mark an event as done."""
    await session.execute(
        update(OutboxEvent).where(OutboxEvent.id == event_id).values(status="done")
    )


async def mark_failed(session: AsyncSession, event: OutboxEvent, *, error: str) -> None:
    """Mark an event as failed."""
    attempts = event.attempts + 1
    delay_sec = min(60 * attempts, 300)
    event.attempts = attempts
    event.status = "pending" if attempts < 5 else "failed"
    event.available_at = datetime.now(timezone.utc) + timedelta(seconds=delay_sec)
    payload = dict(event.payload)
    payload["last_error"] = error
    event.payload = payload
    await session.flush()
