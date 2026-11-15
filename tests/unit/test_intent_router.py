from backend.services.agents.core.router import classify


def test_override_plan() -> None:
    intent, body = classify("/plan ship the release")
    assert intent == "plan"
    assert body == "ship the release"


def test_override_do() -> None:
    intent, body = classify("/do create file notes.md")
    assert intent == "execute"
    assert "create file" in body


def test_execute_keyword() -> None:
    intent, _ = classify("please implement the sync helper")
    assert intent == "execute"


def test_plan_keyword() -> None:
    intent, _ = classify("make a plan for onboarding")
    assert intent == "plan"


def test_converse_default() -> None:
    intent, body = classify("who owes me a reply?")
    assert intent == "converse"
    assert "reply" in body


def test_plan_indeed_jobs() -> None:
    msg = (
        "I want you to find me the list of indeed jobs i have applied to for the AI roles. "
        "I also want you to read their subject and content as a table"
    )
    intent, _ = classify(msg)
    assert intent == "plan"


def test_plan_numbered_steps() -> None:
    msg = "1. Find gmail threads\n2. Show as table"
    intent, _ = classify(msg)
    assert intent == "plan"


def test_calendar_create_routes_execute() -> None:
    intent, _ = classify(
        "Create a 30min test event tomorrow at 4pm IST titled MindOS test."
    )
    assert intent == "execute"


def test_schedule_at_time_routes_execute() -> None:
    intent, _ = classify("call it 'Test 101' AND SCHEDULE IT AT 2PM")
    assert intent == "execute"


def test_delete_meetings_calender_typo_execute() -> None:
    msg = "delete the first 2 upcoming meetings in my calender"
    assert classify(msg)[0] == "execute"
