from backend.services.retrieval.core.hybrid import _merge_chunk_hits, _title_rank_boost


def test_merge_combines_keyword_and_vector() -> None:
    keyword = [
        {
            "chunk_id": "c1",
            "artifact_id": "a",
            "title": "t",
            "source": "s",
            "rank": 1.0,
            "snippet": "k",
        }
    ]
    vec = [
        {
            "chunk_id": "c2",
            "artifact_id": "b",
            "title": "t2",
            "source": "s",
            "rank": 0.8,
            "snippet": "v",
        }
    ]
    merged = _merge_chunk_hits(keyword, vec, limit=5)
    ids = {m["artifact_id"] for m in merged}
    assert ids == {"a", "b"}


def test_title_token_boost() -> None:
    assert _title_rank_boost("what did the pricing team say", "Re: Pricing update") > 1.0
    assert _title_rank_boost("hi", "Pricing update") == 1.0


def test_title_match_boosts_rank() -> None:
    keyword = [
        {
            "chunk_id": "c1",
            "artifact_id": "a",
            "title": "Pricing update",
            "source": "gmail",
            "rank": 0.4,
            "snippet": "body",
        },
        {
            "chunk_id": "c2",
            "artifact_id": "b",
            "title": "Other",
            "source": "gmail",
            "rank": 0.42,
            "snippet": "body",
        },
    ]
    merged = _merge_chunk_hits(
        keyword, [], limit=2, query="what did pricing say"
    )
    assert merged[0]["artifact_id"] == "a"