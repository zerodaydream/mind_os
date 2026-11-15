from backend.services.agents.core.routing.router import classify, needs_url_agent


def test_summarize_pdf_url_routes_plan() -> None:
    msg = "summarize this pdf for me https://arxiv.org/pdf/1803.08664"
    intent, _ = classify(msg)
    assert intent == "plan"


def test_author_followup_with_arxiv_history() -> None:
    hist = [
        {"role": "user", "content": "summarize https://arxiv.org/pdf/1803.08664"},
        {"role": "assistant", "content": "..."},
    ]
    assert needs_url_agent("who are the researchers worked on it", hist) is True