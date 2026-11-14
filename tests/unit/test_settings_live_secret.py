import pytest

from backend.common.utils.enums import LlmMode
from backend.common.config_loader.settings import Settings


def test_live_mode_rejects_default_secret(monkeypatch) -> None:
    monkeypatch.setenv("MINDOS_LLM_MODE", "live")
    monkeypatch.setenv("MINDOS_SECRET_KEY", "change-me")
    with pytest.raises(ValueError, match="MINDOS_SECRET_KEY"):
        Settings()


def test_live_mode_accepts_real_secret(monkeypatch) -> None:
    monkeypatch.setenv("MINDOS_LLM_MODE", "live")
    monkeypatch.setenv("MINDOS_SECRET_KEY", "a" * 32)
    cfg = Settings()
    assert cfg.mindos_llm_mode == LlmMode.LIVE
