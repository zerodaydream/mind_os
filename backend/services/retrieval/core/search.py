"""Artifact search: keyword (Postgres full-text) and helpers."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def search_artifacts_by_keyword(
    session: AsyncSession,
    *,
    user_id,
    query: str,
    limit: int = 20,
    source: str | None = None,
) -> list[dict]:
    """Rank artifacts by keyword match (Postgres ``tsvector`` full-text search)."""
    src_clause = " AND source = :src" if source else ""
    sql = text(
        f"""
        SELECT id, title, source, ts_rank(tsv, websearch_to_tsquery('english', :q)) AS rank
        FROM artifacts
        WHERE user_id = :uid AND tsv @@ websearch_to_tsquery('english', :q){src_clause}
        ORDER BY rank DESC
        LIMIT :lim
        """
    )
    params: dict = {"q": query, "uid": user_id, "lim": limit}
    if source:
        params["src"] = source
    rows = await session.execute(sql, params)
    return [
        {
            "artifact_id": str(r.id),
            "title": r.title,
            "source": r.source,
            "rank": float(r.rank),
        }
        for r in rows
    ]