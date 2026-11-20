"""Parallel retrieval plane: dense + lexical + memories + commitments → RRF → EvidencePack."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.common.config_loader.settings import Settings
from backend.common.database import lifecycle
from backend.services.agents.core.contracts.evidence import Evidence, EvidencePack
from backend.services.agents.core.contracts.turn_plan import TurnPlan
from backend.services.retrieval.core.embed import embed_texts
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.services.retrieval.core.rrf import reciprocal_rank_fusion, rrf_margin
from backend.services.retrieval.core.search_memories import search_memories_ilike


_SOFT_DEADLINE_SEC = 0.45
_RERANK_TOP = 40
_WIDE_MARGIN = 0.02


async def retrieve_evidence_pack(
    session: AsyncSession,
    *,
    user_id,
    settings: Settings,
    plan: TurnPlan,
    message: str,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    limit: int = 12,
) -> EvidencePack:
    """Run parallel retrievers, fuse with RRF, optionally local-rerank, return EvidencePack."""
    if plan.skip_retrieval:
        return EvidencePack(insufficient=True, queries=[], timings_ms={"total": 0.0})
    if session_factory is None:
        session_factory = lifecycle._session_factory
    t0 = time.perf_counter()
    queries = [q.strip() for q in (plan.queries or []) if q and q.strip()]
    if not queries:
        q0 = (plan.task or message or "").strip()
        if q0:
            queries = [q0]
    if not queries:
        return EvidencePack(insufficient=True, queries=[], timings_ms={"total": 0.0})

    sources = list(plan.filters.sources or [])
    source = sources[0] if len(sources) == 1 else None

    async def _hybrid_leg(q: str) -> list[dict]:
        if session_factory is None:
            return await hybrid_search_artifacts(
                session,
                user_id=user_id,
                query=q,
                settings=settings,
                limit=_RERANK_TOP,
                source=source,
            )
        async with session_factory() as s:
            return await hybrid_search_artifacts(
                s,
                user_id=user_id,
                query=q,
                settings=settings,
                limit=_RERANK_TOP,
                source=source,
            )

    async def _memory_leg() -> list[dict]:
        q = queries[0]
        if session_factory is None:
            return await search_memories_ilike(session, user_id=user_id, query=q, limit=8)
        async with session_factory() as s:
            return await search_memories_ilike(s, user_id=user_id, query=q, limit=8)

    async def _commitment_leg() -> list[dict]:
        from sqlalchemy import text

        q = queries[0]
        pat = "%" + q.replace("%", "").replace("_", "")[:80] + "%"
        sql = text(
            """
            SELECT id, summary, status
            FROM commitments
            WHERE user_id = :uid AND status = 'open'
              AND summary ILIKE :pat
            ORDER BY created_at DESC
            LIMIT 8
            """
        )
        if session_factory is None:
            rows = await session.execute(sql, {"uid": user_id, "pat": pat})
        else:
            async with session_factory() as s:
                rows = await s.execute(sql, {"uid": user_id, "pat": pat})
        return [
            {
                "artifact_id": f"commitment:{r.id}",
                "title": "commitment",
                "source": "commitment",
                "snippet": r.summary,
                "rank": 1.0,
                "kind": "commitment",
            }
            for r in rows
        ]

    timed: dict[str, float] = {}
    ranked_lists: list[list[dict]] = []

    async def _timed(name: str, coro):
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(coro, timeout=_SOFT_DEADLINE_SEC)
        except (asyncio.TimeoutError, Exception):
            result = []
        timed[name] = (time.perf_counter() - start) * 1000.0
        return result

    hybrid_tasks = [_timed(f"hybrid_{i}", _hybrid_leg(q)) for i, q in enumerate(queries[:3])]
    results = await asyncio.gather(
        *hybrid_tasks,
        _timed("memories", _memory_leg()),
        _timed("commitments", _commitment_leg()),
    )
    hybrid_results = results[:-2]
    mem_hits = results[-2]
    commit_hits = results[-1]
    for hits in hybrid_results:
        ranked_lists.append(hits)
    if mem_hits:
        ranked_lists.append(
            [
                {
                    "artifact_id": f"memory:{h['memory_id']}",
                    "title": h.get("kind") or "memory",
                    "source": "memory",
                    "snippet": h.get("content") or "",
                    "rank": float(h.get("rank") or 0),
                    "kind": "memory",
                }
                for h in mem_hits
            ]
        )
    if commit_hits:
        ranked_lists.append(commit_hits)

    fused = reciprocal_rank_fusion(ranked_lists, limit=_RERANK_TOP)
    margin = rrf_margin(fused)
    if margin is not None and margin < _WIDE_MARGIN and fused:
        fused = await _local_rerank(fused, queries[0], settings)

    items = _to_evidence(fused[:limit])
    timed["total"] = (time.perf_counter() - t0) * 1000.0
    return EvidencePack(
        items=items,
        insufficient=len(items) == 0,
        index_as_of=datetime.now(timezone.utc),
        queries=queries,
        timings_ms=timed,
        rerank_margin=margin,
    )


async def _local_rerank(
    hits: list[dict[str, Any]], query: str, settings: Settings
) -> list[dict[str, Any]]:
    """Rerank top hits by embedding cosine of query vs title+snippet (no cross-encoder yet)."""
    texts = [
        f"{h.get('title') or ''}\n{(h.get('snippet') or '')[:500]}".strip() for h in hits
    ]
    if not texts:
        return hits

    def _embed_pair() -> tuple[list[float], list[list[float]]]:
        qvec = embed_texts([query], settings.embedding_model, is_query=True)[0]
        dvecs = embed_texts(texts, settings.embedding_model, is_query=False)
        return qvec, dvecs

    try:
        qvec, dvecs = await asyncio.to_thread(_embed_pair)
    except Exception:
        return hits
    scored: list[tuple[float, dict]] = []
    for h, dvec in zip(hits, dvecs, strict=False):
        scored.append((_cosine(qvec, dvec), dict(h)))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, h in scored:
        h["rank"] = score
        out.append(h)
    return out


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _to_evidence(hits: list[dict[str, Any]]) -> list[Evidence]:
    items: list[Evidence] = []
    for i, h in enumerate(hits, start=1):
        kind = str(h.get("kind") or h.get("source") or "artifact")
        items.append(
            Evidence(
                eid=f"E{i}",
                kind=kind,
                text=(h.get("snippet") or "").strip(),
                score=float(h.get("rank") or 0.0),
                artifact_id=str(h.get("artifact_id") or "") or None,
                title=h.get("title"),
                uri=h.get("uri"),
                metadata={k: v for k, v in h.items() if k not in {
                    "snippet", "title", "rank", "artifact_id", "uri", "kind", "source"
                }},
            )
        )
    return items
