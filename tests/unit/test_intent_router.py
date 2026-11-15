from backend.services.agents.core.routing.router import classify


def test_always_plan() -> None:
    for msg in (
        "/plan ship the release",
        "/do create file notes.md",
        "please implement the sync helper",
        "who owes me a reply?",
        "hello",
        "delete the first 2 upcoming meetings in my calender",
    ):
        intent, body = classify(msg)
        assert intent == "plan"
        assert body == msg.strip()