import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import Memory


async def upsert_memories_from_artifact(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    artifact_id: uuid.UUID,
    drafts: list[dict],
) -> int:
    if not drafts:
        return 0
    kinds = {d["kind"] for d in drafts}
    rows = await session.execute(
        select(Memory.kind, Memory.content).where(
            Memory.user_id == user_id,
            Memory.kind.in_(kinds),
        )
    )
    existing = {(k, c) for k, c in rows.all()}

    created = 0
    for draft in drafts:
        content = draft["content"].strip()
        kind = draft["kind"]
        if (kind, content) in existing:
            continue
        session.add(
            Memory(
                user_id=user_id,
                kind=kind,
                content=content,
                source="user",
                approved=True,
                evidence=[{"artifact_id": str(artifact_id)}],
            )
        )
        existing.add((kind, content))
        created += 1
    return created
