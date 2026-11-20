from backend.services.ingest.core.chunker import _CHUNK_TOKEN_BUDGET, _tok, chunk_text

_BGE = "BAAI/bge-base-en-v1.5"


def test_chunker_splits_paragraphs() -> None:
    body = "para one\n\npara two " + ("word " * 900)
    chunks = chunk_text(body, embedding_model=_BGE)
    assert len(chunks) >= 2


def test_chunker_overlap_carries_tail() -> None:
    body = ("alpha " * 700) + "\n\n" + ("beta " * 700)
    chunks = chunk_text(body, embedding_model=_BGE)
    assert len(chunks) >= 2
    assert "alpha" in chunks[1][1]


def test_chunker_splits_attachments() -> None:
    body = "Subject line\n\n### Attachment: a.yml\nversion: 1\n\n### Attachment: b.yml\nversion: 2"
    chunks = chunk_text(body, version="v2", embedding_model=_BGE)
    assert len(chunks) == 3
    assert "### Attachment: a.yml" in chunks[1][1]


def test_chunker_small_single_chunk() -> None:
    body = "### Attachment: tiny.txt\nhello"
    assert len(chunk_text(body, version="v2", embedding_model=_BGE)) == 1


def test_chunks_stay_within_token_budget() -> None:
    body = "token " * 5000 + "\n\n" + "more " * 5000
    for _, chunk in chunk_text(body, version="v2", embedding_model=_BGE):
        assert _tok(chunk, _BGE) <= _CHUNK_TOKEN_BUDGET
