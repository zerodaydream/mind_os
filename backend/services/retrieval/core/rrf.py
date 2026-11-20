"""Reciprocal Rank Fusion — scale-free merge across retrievers."""

from __future__ import annotations

from typing import Any


def reciprocal_rank_fusion(
    ranked_lists: list[list[dict[str, Any]]],
    *,
    id_key: str = "artifact_id",
    k: int = 60,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Fuse ranked hit lists with RRF (k=60). Preserves best snippet/title per id."""
    scores: dict[str, float] = {}
    best: dict[str, dict[str, Any]] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, start=1):
            key = str(hit.get(id_key) or hit.get("chunk_id") or "")
            if not key:
                continue
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            prev = best.get(key)
            if prev is None or float(hit.get("rank") or 0) > float(prev.get("rank") or 0):
                best[key] = dict(hit)
    fused = [{**best[key], "rank": score, "rrf": score} for key, score in scores.items()]
    fused.sort(key=lambda x: x["rank"], reverse=True)
    return fused[:limit]


def rrf_margin(hits: list[dict[str, Any]], *, top: int = 1, vs: int = 8) -> float | None:
    """Score gap between rank `top` and rank `vs` (1-based); None if too short."""
    if len(hits) < vs:
        return None
    a = float(hits[top - 1].get("rank") or 0.0)
    b = float(hits[vs - 1].get("rank") or 0.0)
    return a - b
