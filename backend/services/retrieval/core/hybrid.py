from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.services.retrieval.core.embed import embed_texts
from backend.services.retrieval.core.search import search_artifacts_fts


def _merge_hits(fts: list[dict], vector: list[dict], *, limit: int) -> list[dict]:
    by_id: dict[str, dict] = {}
    max_fts = max((float(r["rank"]) for r in fts), default=0.0) or 1.0
    for r in fts:
        aid = r["artifact_id"]
        by_id[aid] = {
            "artifact_id": aid,
            "title": r.get("title"),
            "source": r.get("source"),
            "fts_rank": float(r["rank"]),
            "vec_rank": 0.0,
        }
    for r in vector:
        aid = r["artifact_id"]
        row = by_id.get(aid)
        if row is None:
            by_id[aid] = {
                "artifact_id": aid,
                "title": r.get("title"),
                "source": r.get("source"),
                "fts_rank": 0.0,
                "vec_rank": float(r["rank"]),
            }
        else:
            row["vec_rank"] = max(row["vec_rank"], float(r["rank"]))
    out: list[dict] = []
    for row in by_id.values():
        score = 0.5 * (row["fts_rank"] / max_fts) + 0.5 * row["vec_rank"]
        out.append({**row, "rank": score})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:limit]


async def _search_artifacts_vector(
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
        SELECT a.id, a.title, a.source,
               1 - MIN(e.embedding <=> CAST(:qvec AS vector)) AS rank
        FROM chunk_embeddings e
        JOIN artifact_chunks c ON c.id = e.chunk_id
        JOIN artifacts a ON a.id = c.artifact_id
        WHERE a.user_id = :uid AND e.embedding_model = :model{src_clause}
        GROUP BY a.id, a.title, a.source
        ORDER BY rank DESC
        LIMIT :lim
        """
    )
    params: dict = {"qvec": vec, "uid": user_id, "model": model, "lim": limit}
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


async def hybrid_search_artifacts(
    session: AsyncSession,
    *,
    user_id,
    query: str,
    settings: Settings,
    limit: int = 20,
    source: str | None = None,
) -> list[dict]:
    fts = await search_artifacts_fts(
        session, user_id=user_id, query=query, limit=limit, source=source
    )
    try:
        qvec = embed_texts([query], settings.embedding_model)[0]
    except RuntimeError:
        return fts
    vector = await _search_artifacts_vector(
        session,
        user_id=user_id,
        query_vec=qvec,
        model=settings.embedding_model,
        limit=limit,
        source=source,
    )
    if not vector:
        return fts
    return _merge_hits(fts, vector, limit=limit)
