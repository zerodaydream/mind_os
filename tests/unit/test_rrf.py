from backend.services.retrieval.core.rrf import reciprocal_rank_fusion, rrf_margin


def test_rrf_prefers_consensus() -> None:
    a = [{"artifact_id": "1", "rank": 0.9, "snippet": "a"}, {"artifact_id": "2", "rank": 0.5}]
    b = [{"artifact_id": "2", "rank": 0.8, "snippet": "b"}, {"artifact_id": "1", "rank": 0.4}]
    fused = reciprocal_rank_fusion([a, b], limit=5)
    assert fused[0]["artifact_id"] in {"1", "2"}
    assert len(fused) == 2


def test_rrf_margin() -> None:
    hits = [{"rank": 0.1 - i * 0.001} for i in range(10)]
    m = rrf_margin(hits, top=1, vs=8)
    assert m is not None and m > 0
