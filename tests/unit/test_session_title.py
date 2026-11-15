from backend.services.agents.core.session.title import display_session_title, needs_session_title


def test_display_session_title() -> None:
    assert display_session_title(None) == "New Chat"
    assert display_session_title("New chat") == "New Chat"
    assert display_session_title("Calendar Next Week") == "Calendar Next Week"


def test_needs_session_title() -> None:
    assert needs_session_title("New chat")
    assert not needs_session_title("Inbox Links Scan")