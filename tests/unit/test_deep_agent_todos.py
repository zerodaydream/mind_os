from backend.services.graph.core.deep_agent import _format_todos_think, _latest_todos


def test_format_todos_think() -> None:
    todos = [
        {"content": "Event A", "status": "in_progress"},
        {"content": "Event B", "status": "pending"},
        {"content": "Event C", "status": "completed"},
    ]
    text = _format_todos_think(todos)
    assert text.startswith("Planning moves:")
    assert "● Event A (in progress)" in text
    assert "○ Event B (pending)" in text
    assert "✓ Event C (completed)" in text


def test_format_todos_string_items() -> None:
    text = _format_todos_think(["Create 1pm event", {"content": "Done", "status": "done"}])
    assert "○ Create 1pm event (pending)" in text
    assert "✓ Done (done)" in text


def test_latest_todos_from_tool_call() -> None:
    msgs = [
        {
            "tool_calls": [
                {
                    "name": "write_todos",
                    "args": {
                        "todos": [{"content": "x", "status": "pending"}],
                    },
                }
            ]
        }
    ]
    assert _latest_todos(msgs) == [{"content": "x", "status": "pending"}]


def test_latest_todos_ignores_prior_turn() -> None:
    msgs = [
        {
            "tool_calls": [
                {
                    "name": "write_todos",
                    "args": {
                        "todos": [{"content": "old", "status": "completed"}],
                    },
                }
            ]
        },
        {"type": "human", "content": "next question"},
        {"type": "ai", "content": "checking"},
    ]
    assert _latest_todos(msgs) is None


def test_latest_todos_current_turn_wins() -> None:
    msgs = [
        {
            "tool_calls": [
                {
                    "name": "write_todos",
                    "args": {"todos": [{"content": "old", "status": "completed"}]},
                }
            ]
        },
        {"type": "human", "content": "next"},
        {
            "tool_calls": [
                {
                    "name": "write_todos",
                    "args": {"todos": [{"content": "new", "status": "pending"}]},
                }
            ]
        },
    ]
    assert _latest_todos(msgs) == [{"content": "new", "status": "pending"}]