from backend.services.graph.core.deep_agent import _clock_block


def test_clock_block_contains_date() -> None:
    block = _clock_block("today 3pm", "Asia/Kolkata")
    assert "Clock" in block
    assert "Asia/Kolkata" in block
    assert "time_zone=Asia/Kolkata" in block


def test_clock_block_tomorrow_hint() -> None:
    block = _clock_block("reminder tomorrow", "Asia/Kolkata")
    assert "Tomorrow=" in block
    assert "tomorrow" in block.lower()