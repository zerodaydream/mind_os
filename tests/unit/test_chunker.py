from backend.services.ingest.core.chunker import chunk_text


def test_chunker_splits_paragraphs() -> None:
    body = "para one\n\npara two " + ("x" * 2000)
    chunks = chunk_text(body)
    assert len(chunks) >= 2
