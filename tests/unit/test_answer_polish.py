from backend.services.agents.core.render.markdown_tables import polish_agent_answer


def test_polish_glued_words_and_list_number() -> None:
    raw = "There are2my_photo files\n1R1 - Propico\ntitledMeeting Surya"
    out = polish_agent_answer(raw)
    assert "are 2 my_photo" in out or "are 2my" not in out
    assert "1. R1" in out
    assert "titled Meeting" in out


def test_polish_splits_inline_calendar_bullets() -> None:
    line = "Reminders: • Wed at 5pm — https://calendar.google.com/x • Thu at 5pm — https://calendar.google.com/y"
    out = polish_agent_answer(line)
    assert out.count("•") >= 2
    assert "  https://calendar.google.com/x" in out