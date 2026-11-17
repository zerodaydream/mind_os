from backend.services.agents.core.converse.converse_synth import _fallback_answer
from backend.services.agents.core.turn.turn_retrieval import TurnRetrieval


def test_fallback_lists_context() -> None:
    ctx = TurnRetrieval(
        message="hi",
        results=[{"artifact_id": "1", "title": "Hello", "source": "gmail"}],
        commitments=["I will reply by Friday"],
        gmail_ctx=[{"title": "Re: budget", "from_addr": "alice@example.com"}],
        has_context=True,
    )
    text = _fallback_answer(ctx)
    assert "Hello" in text
    assert "Friday" in text
    assert "alice" in text
