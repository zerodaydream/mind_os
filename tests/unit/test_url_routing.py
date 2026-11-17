from backend.services.agents.core.routing.router import url_from_thread
from backend.services.agents.core.routing.turn_prep import fallback_turn_prep


def test_summarize_pdf_url_routes_plan() -> None:
    msg = "summarize this pdf for me https://arxiv.org/pdf/1803.08664"
    prep = fallback_turn_prep(msg, None)
    assert prep.intent in ("plan", "execute")
    assert prep.in_scope is True


def test_url_from_thread_followup() -> None:
    url = "https://docs.google.com/spreadsheets/d/abc/edit"
    hist = [
        {"role": "user", "content": f"read this {url}"},
        {"role": "assistant", "content": "Annual budget template…"},
    ]
    q = "see the incomes sheet of this excel and give me paycheck row"
    assert url_from_thread(q, hist) == url