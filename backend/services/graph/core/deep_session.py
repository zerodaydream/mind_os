"""Deep-agent session workspace, LangGraph persistence, and composite FS backend."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from backend.services.agents.core.render.prompt_budget import clip_text

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

SESSION_NOTES_MAX = 2_400
SESSION_TURN_ANSWER_MAX = 3_500


def mindos_home() -> Path:
    """Resolved MINDOS_HOME or cwd for session files and project routes."""
    home = os.environ.get("MINDOS_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve()
    return Path.cwd().resolve()


def session_dir(thread_id) -> Path:
    """Filesystem root for one chat thread's deep-agent workspace."""
    return mindos_home() / ".mindos" / "sessions" / str(thread_id)


def ensure_session_workspace(thread_id) -> Path:
    """Create session dir, NOTES.md, and SESSION.md if missing."""
    root = session_dir(thread_id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "tool-dumps").mkdir(exist_ok=True)
    notes = root / "NOTES.md"
    if not notes.is_file():
        notes.write_text(
            "# Session notes\n\nScratch pad for this chat. The agent may append here.\n",
            encoding="utf-8",
        )
    session_md = root / "SESSION.md"
    if not session_md.is_file():
        session_md.write_text(
            "# Session log\n\nRolling summary of this chat thread.\n",
            encoding="utf-8",
        )
    return root


def read_notes_preview(thread_id) -> str:
    """Clipped NOTES.md for deep-agent context."""
    path = session_dir(thread_id) / "NOTES.md"
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return ""
    return clip_text(raw, SESSION_NOTES_MAX)


def memory_sources_for_thread(thread_id) -> list[str]:
    """Virtual FS paths the agent may read as memory sources.

    Never loads repo AGENTS.md — that contaminates the assistant with coding-agent
    instructions when MINDOS_HOME defaults to the cwd.
    """
    sources = ["/SESSION.md"]
    ensure_session_workspace(thread_id)
    return sources


@lru_cache(maxsize=1)
def deep_checkpointer() -> object | None:
    """Process-wide LangGraph checkpointer (Postgres when available, else MemorySaver)."""
    pg = _postgres_checkpointer()
    if pg is not None:
        return pg
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    except ImportError:
        return None


_postgres_cm: object | None = None


def _postgres_checkpointer() -> object | None:
    """Postgres checkpointer for durable interrupts across process restarts."""
    global _postgres_cm
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        try:
            from backend.common.config_loader.settings import get_settings

            url = get_settings().database_url
        except Exception:
            return None
    if not url:
        return None
    # langgraph-checkpoint-postgres expects a psycopg conn string (not +asyncpg).
    sync_url = url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    try:
        from langgraph.checkpoint.postgres import PostgresSaver

        # from_conn_string returns a context manager; keep it process-wide.
        cm = PostgresSaver.from_conn_string(sync_url)
        saver = cm.__enter__()
        _postgres_cm = cm
        try:
            saver.setup()
        except Exception:
            pass
        return saver
    except Exception:
        return None


@lru_cache(maxsize=1)
def deep_store() -> object | None:
    """Process-wide LangGraph store or None if unavailable."""
    try:
        from langgraph.store.memory import InMemoryStore

        return InMemoryStore()
    except ImportError:
        return None


def build_deep_backend(thread_id) -> BackendProtocol:
    """Composite deepagents backend: session FS, optional explicit project root."""
    from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend

    session_root = ensure_session_workspace(thread_id)
    store = deep_store()
    tid = str(thread_id)

    session_be = FilesystemBackend(root_dir=str(session_root), virtual_mode=True)
    routes: dict[str, BackendProtocol] = {}
    # Only expose /project/ when explicitly configured — never the MindOS repo cwd.
    project_root = os.environ.get("MINDOS_PROJECT_ROOT", "").strip()
    if project_root:
        routes["/project/"] = FilesystemBackend(
            root_dir=str(Path(project_root).expanduser().resolve()),
            virtual_mode=True,
        )
    if store is not None:
        routes["/store/"] = StoreBackend(
            store=store,
            namespace=lambda _rt, _tid=tid: ("mindos", _tid, "vfs"),
        )
    return CompositeBackend(default=session_be, routes=routes)


def append_session_turn(
    thread_id,
    *,
    user_message: str,
    answer: str,
    todos: list[dict] | None,
) -> None:
    ensure_session_workspace(thread_id)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    block = [
        f"\n\n## Turn {stamp}\n",
        f"**User:** {clip_text(user_message.strip(), 1_200)}\n\n",
        f"**Assistant:** {clip_text((answer or '').strip(), SESSION_TURN_ANSWER_MAX)}\n",
    ]
    if todos:
        block.append("\n**Todos:**\n")
        for item in todos:
            status = str(item.get("status") or "pending")
            content = (item.get("content") or "").strip()
            block.append(f"- [{status}] {content}\n")
    path = session_dir(thread_id) / "SESSION.md"
    with path.open("a", encoding="utf-8") as f:
        f.write("".join(block))