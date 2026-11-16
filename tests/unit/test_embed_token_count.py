from backend.services.retrieval.core.embed import embed_token_count


def test_embed_token_count_matches_fastembed() -> None:
    text = "machine learning embeddings"
    n = embed_token_count(text, model_id="BAAI/bge-base-en-v1.5")
    assert 3 <= n <= 12