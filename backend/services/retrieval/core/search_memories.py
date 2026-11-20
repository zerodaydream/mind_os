"""Memory recall — word-overlap score until memory embeddings land in Phase 2c."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_TOKEN = re.compile(r"[a-z0-9]{2,}", re.I)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text or "")}


def score_memory_candidate(
    *,
    query: str,
    content: str,
    updated_at: datetime | None,
    use_count: int = 0,
) -> float:
    """Similarity + mild recency + usefulness. Higher is better."""
    q = _tokens(query)
    c = _tokens(content)
    if not q or not c:
        overlap = 0.0
    else:
        overlap = len(q & c) / float(len(q))
    recency = 0.0
    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 86400.0)
        recency = math.exp(-age_days / 45.0)
    usefulness = min(1.0, math.log1p(max(0, use_count)) / 3.0)
    return 0.65 * overlap + 0.20 * recency + 0.15 * usefulness


async def search_memories_ilike(
    session: AsyncSession, *, user_id, query: str, limit: int = 10
) -> list[dict]:
    """Search approved memories; score by token overlap (not whole-query ILIKE)."""
    tokens = sorted(_tokens(query))[:8]
    if not tokens:
        sql = text(
            """
            SELECT id, kind, content, updated_at
            FROM memories
            WHERE user_id = :uid AND approved = true
            ORDER BY updated_at DESC
            LIMIT :lim
            """
        )
        rows = await session.execute(sql, {"uid": user_id, "lim": limit})
        return [
            {
                "memory_id": str(r.id),
                "kind": r.kind,
                "content": r.content,
                "rank": 0.1,
            }
            for r in rows
        ]

    # OR of token ILIKEs — avoids empty hits when the full query never appears.
    clauses = " OR ".join(f"content ILIKE :p{i}" for i in range(len(tokens)))
    params: dict = {"uid": user_id, "lim": max(limit * 4, 24)}
    for i, tok in enumerate(tokens):
        params[f"p{i}"] = f"%{tok}%"
    sql = text(
        f"""
        SELECT id, kind, content, updated_at
        FROM memories
        WHERE user_id = :uid AND approved = true AND ({clauses})
        ORDER BY updated_at DESC
        LIMIT :lim
        """
    )
    rows = await session.execute(sql, params)
    scored: list[tuple[float, dict]] = []
    for r in rows:
        rank = score_memory_candidate(
            query=query,
            content=r.content,
            updated_at=r.updated_at,
        )
        scored.append(
            (
                rank,
                {
                    "memory_id": str(r.id),
                    "kind": r.kind,
                    "content": r.content,
                    "rank": rank,
                },
            )
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:limit]]
