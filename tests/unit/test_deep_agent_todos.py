from backend.services.graph.core.deep_agent import _format_todos_think, _latest_todos


def test_format_todos_think() -> None:
    todos = [
        {"content": "Event A", "status": "in_progress"},
        {"content": "Event B", "status": "pending"},
        {"content": "Event C", "status": "completed"},
    ]
    text = _format_todos_think(todos)
    assert "→ Event A (in progress)" in text
    assert "○ Event B (pending)" in text
    assert "✓ Event C (completed)" in text


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