from backend.services.integrations.core.gmail_sync import _strip_html


def test_strip_tags() -> None:
    assert "Hello" in _strip_html("<p>Hello <b>world</b></p>")
