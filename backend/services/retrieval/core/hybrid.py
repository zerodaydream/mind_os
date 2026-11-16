"""Combine keyword search and embedding similarity at chunk granularity."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.services.retrieval.core.embed import embed_texts
from backend.services.retrieval.core.search import search_chunks_by_keyword

_HYBRID_KEYWORD_WEIGHT = 0.3
_HYBRID_VECTOR_WEIGHT = 0.7
_CANDIDATE_MULT = 4
_SNIPPET_CHARS = 500


def _merge_chunk_hits(keyword: list[dict], vector: list[dict], *, limit: int) -> list[dict]:
    by_id: dict[str, dict] = {}
    max_keyword = max((float(r["rank"]) for r in keyword), default=0.0) or 1.0
    for r in keyword:
        cid = r["chunk_id"]
        by_id[cid] = {
            "chunk_id": cid,
            "artifact_id": r["artifact_id"],
            "title": r.get("title"),
            "source": r.get("source"),
            "snippet": r.get("snippet") or "",
            "keyword_rank": float(r["rank"]),
            "vec_rank": 0.0,
        }
    for r in vector:
        cid = r["chunk_id"]
        row = by_id.get(cid)
        if row is None:
            by_id[cid] = {
                "chunk_id": cid,
                "artifact_id": r["artifact_id"],
                "title": r.get("title"),
                "source": r.get("source"),
                "snippet": r.get("snippet") or "",
                "keyword_rank": 0.0,
                "vec_rank": float(r["rank"]),
            }
        else:
            row["vec_rank"] = max(row["vec_rank"], float(r["rank"]))
            if not row["snippet"] and r.get("snippet"):
                row["snippet"] = r["snippet"]
    out: list[dict] = []
    for row in by_id.values():
        score = (
            _HYBRID_KEYWORD_WEIGHT * (row["keyword_rank"] / max_keyword)
            + _HYBRID_VECTOR_WEIGHT * row["vec_rank"]
        )
        out.append({**row, "rank": score})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:limit]


async def _search_chunks_vector(
    session: AsyncSession,
    *,
    user_id,
    query_vec: list[float],
    model: str,
    limit: int,
    source: str | None = None,
) -> list[dict]:
    vec = "[" + ",".join(str(float(x)) for x in query_vec) + "]"
    src_clause = " AND a.source = :src" if source else ""
    sql = text(
        f"""
        SELECT c.id AS chunk_id, a.id, a.title, a.source,
               1 - (e.embedding <=> CAST(:qvec AS vector)) AS rank,
               LEFT(c.text, :snip) AS snippet
        FROM chunk_embeddings e
        JOIN artifact_chunks c ON c.id = e.chunk_id
        JOIN artifacts a ON a.id = c.artifact_id
        WHERE a.user_id = :uid AND e.embedding_model = :model{src_clause}
        ORDER BY e.embedding <=> CAST(:qvec AS vector)
        LIMIT :lim
        """
    )
    params: dict = {
        "qvec": vec,
        "uid": user_id,
        "model": model,
        "lim": limit,
        "snip": _SNIPPET_CHARS,
    }
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


async def hybrid_search_artifacts(
    session: AsyncSession,
    *,
    user_id,
    query: str,
    settings: Settings,
    limit: int = 20,
    source: str | None = None,
) -> list[dict]:
    """Hybrid chunk search; each hit includes the best-matching chunk snippet."""
    pool = min(max(limit * _CANDIDATE_MULT, limit), 100)
    keyword_hits = await search_chunks_by_keyword(
        session, user_id=user_id, query=query, limit=pool, source=source
    )
    try:
        qvec = embed_texts([query], settings.embedding_model, is_query=True)[0]
    except RuntimeError:
        return keyword_hits[:limit]
    vector = await _search_chunks_vector(
        session,
        user_id=user_id,
        query_vec=qvec,
        model=settings.embedding_model,
        limit=pool,
        source=source,
    )
    if not vector:
        return keyword_hits[:limit]
    return _merge_chunk_hits(keyword_hits, vector, limit=limit)