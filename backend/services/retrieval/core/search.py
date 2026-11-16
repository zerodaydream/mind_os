"""Artifact and chunk search: Postgres full-text + helpers."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_SNIPPET_CHARS = 500


async def search_chunks_by_keyword(
    session: AsyncSession,
    *,
    user_id,
    query: str,
    limit: int = 20,
    source: str | None = None,
) -> list[dict]:
    """Rank chunks by full-text match on chunk body."""
    src_clause = " AND a.source = :src" if source else ""
    sql = text(
        f"""
        SELECT c.id AS chunk_id, a.id, a.title, a.source,
               ts_rank(c.text_tsv, websearch_to_tsquery('english', :q)) AS rank,
               LEFT(c.text, :snip) AS snippet
        FROM artifact_chunks c
        JOIN artifacts a ON a.id = c.artifact_id
        WHERE a.user_id = :uid
          AND c.text_tsv @@ websearch_to_tsquery('english', :q){src_clause}
        ORDER BY rank DESC
        LIMIT :lim
        """
    )
    params: dict = {"q": query, "uid": user_id, "lim": limit, "snip": _SNIPPET_CHARS}
    if source:
        params["src"] = source
    rows = await session.execute(sql, params)
    return [
        {
            "chunk_id": str(r.chunk_id),
            "artifact_id": str(r.id),
            "title": r.title,
            "source": r.source,
            "rank": float(r.rank),
            "snippet": (r.snippet or "").strip(),
        }
        for r in rows
    ]