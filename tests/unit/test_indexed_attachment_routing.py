from backend.services.agents.core.turn.turn_retrieval import looks_like_named_attachment


def test_named_pdf_without_path() -> None:
    q = "Project 1_ Hyderbad residensial properties landscape.pdf tell me about this"
    assert looks_like_named_attachment(q)


def test_local_path_not_named_attachment() -> None:
    assert not looks_like_named_attachment("read ~/Downloads/foo.pdf")
    assert not looks_like_named_attachment("summarize /Users/me/foo.pdf")