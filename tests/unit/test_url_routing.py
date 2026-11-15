from backend.services.agents.core.routing.router import (
    classify,
    needs_url_agent,
    url_from_thread,
)


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


def test_spreadsheet_tab_followup_routes_url_agent() -> None:
    url = "https://docs.google.com/spreadsheets/d/abc/edit"
    hist = [
        {"role": "user", "content": f"read this {url}"},
        {"role": "assistant", "content": "Annual budget template…"},
    ]
    q = "see the incomes sheet of this excel and give me paycheck row"
    assert needs_url_agent(q, hist) is True
    assert url_from_thread(q, hist) == url