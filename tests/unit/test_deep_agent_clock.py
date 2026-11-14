from backend.services.graph.core.deep_agent import _clock_block, _guess_tz


def test_guess_tz_ist() -> None:
    assert _guess_tz("meeting at 3pm IST") == "Asia/Kolkata"


def test_clock_block_contains_date() -> None:
    block = _clock_block("today 3pm Asia/Kolkata")
    assert "Clock" in block
    assert "Asia/Kolkata" in block