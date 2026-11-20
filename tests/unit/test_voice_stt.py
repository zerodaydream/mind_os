import pytest

from cli import voice_stt


def test_recorder_options_moonshine() -> None:
    opts = voice_stt.recorder_options()
    assert opts["language"] == "en"
    assert opts["backend"] == "moonshine"


def test_stt_status_when_installed() -> None:
    pytest.importorskip("moonshine_voice")
    ok, _ = voice_stt.stt_status()
    assert ok is True