from backend.services.retrieval.core.hybrid import _merge_hits


def test_merge_combines_fts_and_vector() -> None:
    fts = [{"artifact_id": "a", "title": "t", "source": "s", "rank": 1.0}]
    vec = [{"artifact_id": "b", "title": "t2", "source": "s", "rank": 0.8}]
    merged = _merge_hits(fts, vec, limit=5)
    ids = {m["artifact_id"] for m in merged}
    assert ids == {"a", "b"}
