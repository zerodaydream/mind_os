import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import ArtifactChunk, Commitment, Tension


async def _open_tension_keys(session: AsyncSession, user_id: uuid.UUID) -> set[tuple[str, str]]:
    rows = await session.execute(
        select(Tension.tension_type, Tension.description).where(
            Tension.user_id == user_id,
            Tension.status == "open",
        )
    )
    return {(t, d) for t, d in rows.all()}


async def _chunk_artifact_map(
    session: AsyncSession, chunk_ids: set[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    if not chunk_ids:
        return {}
    rows = await session.execute(
        select(ArtifactChunk.id, ArtifactChunk.artifact_id).where(ArtifactChunk.id.in_(chunk_ids))
    )
    return {cid: aid for cid, aid in rows.all()}


async def _open_commitments(session: AsyncSession, user_id: uuid.UUID) -> list[Commitment]:
    rows = await session.execute(
        select(Commitment).where(Commitment.user_id == user_id, Commitment.status == "open")
    )
    return list(rows.scalars())


def _append_tension(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    existing: set[tuple[str, str]],
    tension_type: str,
    description: str,
    artifact_ids: list[uuid.UUID],
    severity: str,
) -> bool:
    key = (tension_type, description)
    if key in existing:
        return False
    session.add(
        Tension(
            user_id=user_id,
            tension_type=tension_type,
            severity=severity,
            status="open",
            artifact_ids=artifact_ids,
            description=description,
        )
    )
    existing.add(key)
    return True


def _detect_duplicate(
    session: AsyncSession,
    user_id: uuid.UUID,
    commitments: list[Commitment],
    chunk_map: dict[uuid.UUID, uuid.UUID],
    existing: set[tuple[str, str]],
) -> int:
    by_summary: dict[str, list[Commitment]] = {}
    for c in commitments:
        by_summary.setdefault(c.summary.strip().lower(), []).append(c)
    created = 0
    for group in by_summary.values():
        if len(group) < 2:
            continue
        desc = f"Duplicate commitments: {group[0].summary[:500]}"
        artifact_ids: list[uuid.UUID] = []
        for c in group:
            if c.source_chunk_id and c.source_chunk_id in chunk_map:
                artifact_ids.append(chunk_map[c.source_chunk_id])
        if _append_tension(
            session,
            user_id=user_id,
            existing=existing,
            tension_type="duplicate_commitment",
            description=desc,
            artifact_ids=list(dict.fromkeys(artifact_ids)),
            severity="medium",
        ):
            created += 1
    return created


def _detect_time_drift(
    session: AsyncSession,
    user_id: uuid.UUID,
    commitments: list[Commitment],
    chunk_map: dict[uuid.UUID, uuid.UUID],
    existing: set[tuple[str, str]],
    *,
    now: datetime,
) -> int:
    created = 0
    for c in commitments:
        if c.due_at is None or c.due_at >= now:
            continue
        desc = f"Past due commitment: {c.summary[:500]}"
        artifact_ids: list[uuid.UUID] = []
        if c.source_chunk_id and c.source_chunk_id in chunk_map:
            artifact_ids = [chunk_map[c.source_chunk_id]]
        if _append_tension(
            session,
            user_id=user_id,
            existing=existing,
            tension_type="time_drift",
            description=desc,
            artifact_ids=artifact_ids,
            severity="high",
        ):
            created += 1
    return created


def _detect_subject_mismatch(
    session: AsyncSession,
    user_id: uuid.UUID,
    commitments: list[Commitment],
    chunk_map: dict[uuid.UUID, uuid.UUID],
    existing: set[tuple[str, str]],
) -> int:
    by_artifact: dict[uuid.UUID, list[Commitment]] = {}
    for c in commitments:
        if not c.source_chunk_id or c.source_chunk_id not in chunk_map:
            continue
        by_artifact.setdefault(chunk_map[c.source_chunk_id], []).append(c)
    created = 0
    for artifact_id, group in by_artifact.items():
        parties = {c.counterparty.strip().lower() for c in group if c.counterparty and c.counterparty.strip()}
        if len(parties) < 2:
            continue
        desc = f"Conflicting counterparties on one artifact: {', '.join(sorted(parties))[:500]}"
        if _append_tension(
            session,
            user_id=user_id,
            existing=existing,
            tension_type="subject_mismatch",
            description=desc,
            artifact_ids=[artifact_id],
            severity="medium",
        ):
            created += 1
    return created


async def detect_duplicate_commitments(session: AsyncSession, user_id: uuid.UUID) -> int:
    existing = await _open_tension_keys(session, user_id)
    commitments = await _open_commitments(session, user_id)
    chunk_map = await _chunk_artifact_map(
        session, {c.source_chunk_id for c in commitments if c.source_chunk_id}
    )
    return _detect_duplicate(session, user_id, commitments, chunk_map, existing)


async def run_tension_detection(session: AsyncSession, user_id: uuid.UUID) -> int:
    existing = await _open_tension_keys(session, user_id)
    commitments = await _open_commitments(session, user_id)
    chunk_map = await _chunk_artifact_map(
        session, {c.source_chunk_id for c in commitments if c.source_chunk_id}
    )
    now = datetime.now(timezone.utc)
    n = 0
    n += _detect_duplicate(session, user_id, commitments, chunk_map, existing)
    n += _detect_time_drift(session, user_id, commitments, chunk_map, existing, now=now)
    n += _detect_subject_mismatch(session, user_id, commitments, chunk_map, existing)
    return n
