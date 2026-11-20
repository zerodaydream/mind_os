import pytest

from backend.common.config_loader.settings import Settings
from backend.common.utils.enums import LlmMode


def test_file_scout_tools_are_read_only():
    from backend.services.integrations.mcp.filesystem_tools import build_filesystem_tools

    tools = build_filesystem_tools(scout_only=True)
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in tools}
    assert "files_find" in names
    assert "files_read" in names
    assert "mindos_fs_write" not in names
    assert "mindos_fs_delete" not in names
    assert "mindos_fs_rename" not in names


def test_subagents_include_file_scout(monkeypatch):
    pytest.importorskip("langchain_core")
    monkeypatch.setenv("MINDOS_FS_ROOTS", "/tmp")

    from backend.services.graph.core.subagents import build_subagents
    from backend.services.integrations.mcp.personal_tools import build_personal_tools

    class _Sess:
        pass

    class _User:
        id = "00000000-0000-0000-0000-000000000001"

    settings = Settings(mindos_llm_mode=LlmMode.MOCK)
    personal = build_personal_tools(_Sess(), _User(), settings)
    subs = build_subagents(personal_tools=personal)
    names = {s["name"] for s in subs}
    assert "researcher" in names
    assert "calendar-operator" in names
    assert "file-scout" in names
    scout = next(s for s in subs if s["name"] == "file-scout")
    scout_tool_names = {
        getattr(t, "name", None) or getattr(t, "__name__", "") for t in scout["tools"]
    }
    assert "files_find" in scout_tool_names
    assert not any("write" in n or "delete" in n for n in scout_tool_names)
