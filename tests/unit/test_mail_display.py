from backend.services.agents.core.render.mail_display import format_mail_display_when


def test_format_mail_display_when_iso() -> None:
    text = format_mail_display_when("2026-11-13T13:24:59+00:00")
    assert "13" in text
    assert "Nov" in text
    assert "2026" in text
    assert "T13:24" not in text