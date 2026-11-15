from backend.services.agents.core.converse.chat import _format_history
from backend.services.agents.core.session.sessions import _title_from


def test_format_history_roles() -> None:
    text = _format_history(
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert "User: hi" in text
    assert "Assistant: hello" in text


def test_title_truncates() -> None:
    long = "x" * 80
    assert len(_title_from(long)) <= 60
    assert _title_from("  hello  ") == "hello"
