from backend.services.graph.core.deep_agent import _surface_tool_outputs


def test_surface_tool_outputs_adds_calendar_link() -> None:
    tool = (
        "Created calendar event: Test 101 at 2025-02-18T14:00:00+05:30. "
        "Link: https://www.google.com/calendar/event?eid=abc"
    )
    result = {
        "messages": [
            {"type": "tool", "content": tool},
            {"type": "ai", "content": "Done — Test 101 is on your calendar."},
        ]
    }
    answer = _surface_tool_outputs("Done — Test 101 is on your calendar.", result)
    assert answer.endswith(
        "https://www.google.com/calendar/event?eid=abc"
    )
    assert answer.count("https://www.google.com/calendar/event?eid=abc") == 1
    assert "Created calendar event" not in answer


def test_surface_tool_outputs_ignores_list_dump() -> None:
    list_tool = "\n".join(
        f"- Event{i} @ now (id=x) Link: https://www.google.com/calendar/event?eid=e{i}"
        for i in range(5)
    )
    body = "Please confirm deleting Event0 and Event1."
    result = {
        "messages": [
            {"type": "tool", "content": list_tool},
            {"type": "ai", "content": body},
        ]
    }
    assert _surface_tool_outputs(body, result) == body


def test_surface_tool_outputs_skips_duplicate_link() -> None:
    url = "https://www.google.com/calendar/event?eid=abc123"
    tool = f"Created calendar event: Test 102. Link: {url}"
    body = f'Scheduled. [Calendar]({url})'
    result = {
        "messages": [
            {"type": "tool", "content": tool},
            {"type": "ai", "content": body},
        ]
    }
    assert _surface_tool_outputs(body, result) == body