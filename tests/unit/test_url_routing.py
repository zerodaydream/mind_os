from backend.services.agents.core.routing.router import classify, url_from_thread


def test_summarize_pdf_url_routes_plan() -> None:
    msg = "summarize this pdf for me https://arxiv.org/pdf/1803.08664"
    intent, _ = classify(msg)
    assert intent == "plan"


def test_url_from_thread_followup() -> None:
    url = "https://docs.google.com/spreadsheets/d/abc/edit"
    hist = [
        {"role": "user", "content": f"read this {url}"},
        {"role": "assistant", "content": "Annual budget template…"},
    ]
    q = "see the incomes sheet of this excel and give me paycheck row"
    assert url_from_thread(q, hist) == url