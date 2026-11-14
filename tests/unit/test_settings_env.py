from backend.common.utils.enums import LlmMode
from backend.common.config_loader.settings import Settings


def test_settings_reads_env_aliases(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@host/db")
    monkeypatch.setenv("MINDOS_LLM_MODE", "mock")
    monkeypatch.setenv("MINDOS_LLM_KILL_SWITCH", "1")
    cfg = Settings()
    assert "host" in cfg.database_url
    assert cfg.mindos_llm_mode == LlmMode.MOCK
    assert cfg.mindos_llm_kill_switch is True
