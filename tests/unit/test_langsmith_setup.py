import os

from backend.common.config_loader.settings import Settings
from backend.common.langsmith_setup import (
    begin_turn_trace,
    configure_langsmith,
    tracing_active,
    unique_run_name,
)


def test_unique_run_name_stable_per_turn():
    begin_turn_trace()
    assert unique_run_name("mindos_handle_turn") == "mindos_handle_turn"
    assert unique_run_name("mindos_convert_file") == "mindos_convert_file"


def test_configure_langsmith_sets_env(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_AGENT_ID", raising=False)
    monkeypatch.delenv("LANGSMITH_AGENT_ENVIRONMENT", raising=False)
    import backend.common.langsmith_setup as mod

    mod._configured = False
    ok = configure_langsmith(
        Settings(
            langsmith_tracing=True,
            langsmith_api_key="lsv2_test",
            langsmith_project="mind-os-test",
            langsmith_agent_id="",
            langsmith_agent_environment="",
        )
    )
    assert ok is True
    assert tracing_active()
    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_test"
    assert os.environ["LANGSMITH_PROJECT"] == "mind-os-test"


def test_configure_agent_addressing(monkeypatch):
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    monkeypatch.delenv("LANGSMITH_AGENT_ID", raising=False)
    import backend.common.langsmith_setup as mod

    mod._configured = False
    ok = configure_langsmith(
        Settings(
            langsmith_tracing=True,
            langsmith_api_key="lsv2_test",
            langsmith_agent_id="mind-os",
            langsmith_agent_environment="local",
        )
    )
    assert ok is True
    assert os.environ["LANGSMITH_AGENT_ID"] == "mind-os"
    assert os.environ["LANGSMITH_AGENT_ENVIRONMENT"] == "local"
    assert "LANGSMITH_PROJECT" not in os.environ


def test_configure_skipped_without_key(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    import backend.common.langsmith_setup as mod

    mod._configured = False
    ok = configure_langsmith(Settings(langsmith_tracing=True, langsmith_api_key=""))
    assert ok is False
    assert not tracing_active()