from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def search_memories_ilike(
    session: AsyncSession, *, user_id, query: str, limit: int = 10
) -> list[dict]:
    """Search memories using ILIKE."""
    # shortcut: ILIKE, add tsvector index when memory volume grows
    pat = "%" + query.replace("%", "").replace("_", "") + "%"
    sql = text(
        """
        SELECT id, kind, content
        FROM memories
        WHERE user_id = :uid AND approved = true AND content ILIKE :pat
        ORDER BY updated_at DESC
        LIMIT :lim
        """
    )
    rows = await session.execute(sql, {"uid": user_id, "pat": pat, "lim": limit})
    return [
        {"memory_id": str(r.id), "kind": r.kind, "content": r.content, "rank": 1.0}
        for r in rows
    ]
