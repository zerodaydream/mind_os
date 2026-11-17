from backend.services.graph.core.deep_agent import _thinking_text_from_message


def test_thinking_text_from_gemini_block() -> None:
    msg = {
        "role": "assistant",
        "content": [
            {
                "type": "thinking",
                "thinking": "Indexed mail first for this PDF—not disk.",
            },
            {"type": "text", "text": "I'll search."},
        ],
        "tool_calls": [{"id": "1", "name": "mindos_search"}],
    }
    assert "Indexed mail" in _thinking_text_from_message(msg)
    assert "I'll search" not in _thinking_text_from_message(msg)