import uuid

import pytest

from backend.services.graph.core.deep_session import (
    append_session_turn,
    ensure_session_workspace,
    memory_sources_for_thread,
    read_notes_preview,
    session_dir,
)


def test_session_dir_under_mindos_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDOS_HOME", str(tmp_path))
    tid = uuid.uuid4()
    root = ensure_session_workspace(tid)
    assert root == session_dir(tid)
    assert root.is_dir()
    assert (root / "NOTES.md").is_file()
    assert (root / "SESSION.md").is_file()
    assert (root / "tool-dumps").is_dir()


def test_memory_sources_include_agents_md(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDOS_HOME", str(tmp_path))
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    tid = uuid.uuid4()
    sources = memory_sources_for_thread(tid)
    assert "/SESSION.md" in sources
    assert "/project/AGENTS.md" in sources


def test_append_session_turn_appends(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDOS_HOME", str(tmp_path))
    tid = uuid.uuid4()
    append_session_turn(
        tid,
        user_message="hello",
        answer="world",
        todos=[{"content": "step", "status": "pending"}],
    )
    text = (session_dir(tid) / "SESSION.md").read_text(encoding="utf-8")
    assert "hello" in text
    assert "world" in text
    assert "[pending] step" in text


def test_read_notes_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDOS_HOME", str(tmp_path))
    tid = uuid.uuid4()
    ensure_session_workspace(tid)
    (session_dir(tid) / "NOTES.md").write_text("keep this note", encoding="utf-8")
    assert "keep this note" in read_notes_preview(tid)


def test_build_deep_backend_imports():
    pytest.importorskip("deepagents")
    from backend.services.graph.core.deep_session import build_deep_backend

    be = build_deep_backend(uuid.uuid4())
    assert be is not None