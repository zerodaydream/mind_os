from __future__ import annotations

import uuid
import warnings
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from cli import term, voice_stt
from cli.async_util import run_async, start_repl_loop, stop_repl_loop
from cli.env import bootstrap_env
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings, reload_settings
from backend.common.langsmith_setup import flush_traces
from backend.common.database import lifecycle
from backend.services.agents.core.turn.orchestrate import handle_turn
from backend.services.agents.core.session.sessions import delete_session, get_session, list_sessions
from backend.services.ingest.core.manual import ensure_default_user
from backend.worker.bootstrap import bootstrap, worker_services
from backend.services.integrations.core.calendar.timezone import detect_client_iana_timezone


def _rel_time(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((datetime.now(timezone.utc) - dt).total_seconds()))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


async def _turn(
    session: AsyncSession,
    message: str,
    gateway,
    session_id: uuid.UUID | None,
    *,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
) -> dict:
    settings = get_settings()
    user = await ensure_default_user(session, settings)
    return await handle_turn(
        session,
        user,
        settings,
        gateway,
        message,
        session_id=session_id,
        on_partial=on_partial,
        client_tz=client_tz,
    )


async def _answer_for(
    message: str,
    gateway,
    session_id: uuid.UUID | None,
    *,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
) -> tuple[str, uuid.UUID]:
    if lifecycle._session_factory is None:
        raise RuntimeError("database not initialized — check DATABASE_URL and Postgres")
    try:
        async with lifecycle._session_factory() as session:
            out = await _turn(
                session, message, gateway, session_id,
                on_partial=on_partial, client_tz=client_tz,
            )
            await session.commit()
    finally:
        flush_traces()
    answer = (out.get("answer") or "").strip() or "(empty)"
    sources = out.get("sources") or []
    intent = out.get("intent")
    if sources and intent in ("plan", "execute"):
        lines = [
            f"  {i}. [{s.get('source') or '?'}] "
            f"{s.get('title') or s.get('artifact_id') or ''}"
            for i, s in enumerate(sources[:15], 1)
        ]
        if len(sources) > 15:
            lines.append(f"  … +{len(sources) - 15} more")
        answer = f"{answer}\n\nsources:\n" + "\n".join(lines)
    return answer, uuid.UUID(out["session_id"])


async def _history_rows() -> list[tuple[str, str, str]]:
    """Return (session_id, title, meta) for the TUI history picker."""
    if lifecycle._session_factory is None:
        return [("__new__", "New chat", "")]
    settings = get_settings()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        rows = await list_sessions(session, user_id=user.id, limit=40)
    values: list[tuple[str, str, str]] = [("__new__", "New chat", "fresh session")]
    for r in rows:
        when = _rel_time(r.get("updated_at"))
        meta = f"{r['n_msgs']} msgs · {when}".strip(" ·")
        values.append((str(r["id"]), (r["title"] or "Untitled")[:48], meta))
    return values


async def _remove_session(session_id: uuid.UUID) -> bool:
    if lifecycle._session_factory is None:
        return False
    settings = get_settings()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        ok = await delete_session(session, session_id=session_id, user_id=user.id)
        if ok:
            await session.commit()
        return ok


async def _load_session(session_id: uuid.UUID) -> tuple[list[tuple[str, str]], uuid.UUID] | None:
    if lifecycle._session_factory is None:
        return None
    settings = get_settings()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        chat = await get_session(session, session_id=session_id, user_id=user.id)
        if chat is None:
            return None
        history = [
            ("you" if m.role == "user" else "assistant", m.content)
            for m in chat.messages
            if m.role in ("user", "assistant")
        ]
        return history, chat.id


def _bootstrap_chat(session_id: uuid.UUID | None) -> tuple[dict, object | None, list[tuple[str, str]]]:
    bootstrap_env()
    settings = reload_settings()
    if settings.langsmith_tracing:
        try:
            import langsmith  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "LANGSMITH_TRACING=true but langsmith is not installed in this Python. "
                "From the repo root run: uv run mindos chat  (or: .venv/bin/mindos chat). "
                "Do not use a global `mindos` on PATH unless you reinstall it from this repo."
            )
    bootstrap()
    services = worker_services()
    gateway = services.llm if services else None
    state: dict = {"session_id": session_id}
    if session_id is not None:
        loaded = run_async(_load_session(session_id))
        initial_history = loaded[0] if loaded else []
        if loaded:
            state["session_id"] = loaded[1]
    else:
        initial_history = []
    if term.in_tui() and voice_stt.stt_status()[0]:
        try:
            voice_stt.warm()
        except Exception:
            pass
    return state, gateway, initial_history


def cmd_chat(*, session_id: uuid.UUID | None = None) -> int:
    cfg = get_config()
    if not cfg.is_enabled("agents"):
        term.action_begin()
        term.error("agents disabled in backend/config.json")
        term.action_end()
        return 1
    if not cfg.is_enabled("retrieval"):
        term.action_begin()
        term.error("retrieval disabled in backend/config.json")
        term.action_end()
        return 1
    start_repl_loop()
    try:
        if term.in_tui():
            state, gateway, initial_history = term.tui_busy(
                lambda: _bootstrap_chat(session_id),
                label="Loading chat",
            )
        else:
            state, gateway, initial_history = _bootstrap_chat(session_id)
    except Exception:
        stop_repl_loop()
        raise

    client_tz = detect_client_iana_timezone()

    def turn(msg: str, on_partial: Callable[[str], None] | None = None) -> str:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            ans, sid = run_async(
                _answer_for(
                    msg,
                    gateway,
                    state["session_id"],
                    on_partial=on_partial,
                    client_tz=client_tz,
                )
            )
        state["session_id"] = sid
        return ans
    def list_history() -> list[tuple[str, str, str]]:
        return run_async(_history_rows())

    def load_history(sid: str) -> list[tuple[str, str]] | None:
        if sid == "__new__":
            state["session_id"] = None
            return []
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            return None
        loaded = run_async(_load_session(uid))
        if loaded is None:
            return None
        hist, chat_id = loaded
        state["session_id"] = chat_id
        return hist

    def delete_history(sid: str) -> bool:
        if sid == "__new__":
            return False
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            return False
        ok = run_async(_remove_session(uid))
        if ok and state.get("session_id") == uid:
            state["session_id"] = None
        return ok

    try:
        if term.in_tui():
            try:
                term.chat_conversation(
                    turn,
                    initial_history=initial_history,
                    list_history=list_history,
                    load_history=load_history,
                    delete_history=delete_history,
                )
            except Exception as exc:
                term.action_begin()
                term.error(f"chat ended: {exc}")
                term.action_end()
            return 0

        # shortcut: non-TUI fallback — one line at a time, upgrade when headless needs history
        def _cli_think(text: str, *, phase: str = "answer") -> None:
            if phase == "think":
                term.note(f"  {text}")

        while True:
            message = term.prompt_line("ask › ")
            if not message:
                break
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    ans, sid = run_async(
                        _answer_for(
                            message,
                            gateway,
                            state["session_id"],
                            on_partial=_cli_think,
                            client_tz=client_tz,
                        )
                    )
                    state["session_id"] = sid
                term.note_md(ans)
            except KeyboardInterrupt:
                break
            except Exception as exc:
                term.error(str(exc))
                return 1
        return 0
    finally:
        stop_repl_loop()
