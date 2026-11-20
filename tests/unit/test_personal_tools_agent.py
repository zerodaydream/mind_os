import pytest

from backend.common.config_loader.settings import Settings
from backend.common.utils.enums import LlmMode


def test_personal_tools_exclude_sync():
    pytest.importorskip("langchain_core")

    from backend.services.integrations.mcp.personal_tools import build_personal_tools

    class _Sess:
        pass

    class _User:
        id = "00000000-0000-0000-0000-000000000001"

    settings = Settings(mindos_llm_mode=LlmMode.MOCK)
    tools = build_personal_tools(_Sess(), _User(), settings)
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}
    assert "search" in names
    assert "calendar_query" in names
    assert "calendar_change" in names
    assert "mindos_gmail_sync" not in names
    assert "mindos_process_outbox" not in names