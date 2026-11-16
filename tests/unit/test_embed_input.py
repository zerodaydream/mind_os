from backend.services.retrieval.core.embed import _embed_input


def test_bge_query_instruction() -> None:
    q = _embed_input("BAAI/bge-base-en-v1.5", "budget", is_query=True)
    assert q.startswith("Represent this sentence for searching relevant passages:")
    assert _embed_input("BAAI/bge-base-en-v1.5", "budget", is_query=False) == "budget"