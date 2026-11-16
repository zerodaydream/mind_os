from __future__ import annotations

import threading
import uuid
import warnings
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from cli import term, voice_stt
from cli.async_util import (
    run_async,
    shared_event_loop_is_running,
    start_shared_event_loop,
    stop_shared_event_loop,
)
from cli.env import bootstrap_env
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings, reload_settings
from backend.common.langsmith_setup import flush_traces
from backend.common.database import lifecycle
from backend.services.agents.core.session.title import display_session_title
from backend.services.agents.core.turn.orchestrate import handle_turn
from backend.services.agents.core.session.sessions import delete_session, get_session, list_sessions
from backend.services.ingest.core.manual import ensure_default_user
from backend.worker.bootstrap import bootstrap, worker_services
from backend.services.integrations.core.calendar.timezone import detect_client_iana_timezone

_chat_warmup_lock = threading.Lock()
_chat_warmup_thread: threading.Thread | None = None
_chat_warmup_result: (
    tuple[dict, object | None, list[tuple[str, str]], str] | None
) = None


def format_relative_timestamp(dt: datetime | None) -> str:
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


async def run_agent_turn(
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


async def fetch_chat_reply(
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
            out = await run_agent_turn(
                session, message, gateway, session_id,
                on_partial=on_partial, client_tz=client_tz,
            )
            await session.commit()
    finally:
        flush_traces()
    answer = (out.get("answer") or "").strip() or "(empty)"
    sources = out.get("sources") or []
    intent = out.get("intent")
    mode = out.get("mode") or ""
    if sources and intent in ("plan", "execute") and mode in ("synth", "plan_synth", "retrieval_only"):
        lines = [
            f"  {i}. [{s.get('source') or '?'}] "
            f"{s.get('title') or s.get('artifact_id') or ''}"
            for i, s in enumerate(sources[:15], 1)
        ]
        if len(sources) > 15:
            lines.append(f"  … +{len(sources) - 15} more")
        answer = f"{answer}\n\nsources:\n" + "\n".join(lines)
    return answer, uuid.UUID(out["session_id"])


async def list_chat_sessions_for_picker() -> list[tuple[str, str, str]]:
    """Return (session_id, title, meta) for the TUI history picker."""
    if lifecycle._session_factory is None:
        return [("__new__", "New chat", "")]
    settings = get_settings()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        rows = await list_sessions(session, user_id=user.id, limit=40)
    values: list[tuple[str, str, str]] = [("__new__", "New chat", "fresh session")]
    for r in rows:
        when = format_relative_timestamp(r.get("updated_at"))
        meta = f"{r['n_msgs']} msgs · {when}".strip(" ·")
        values.append((str(r["id"]), (r["title"] or "Untitled")[:48], meta))
    return values


async def delete_chat_session(session_id: uuid.UUID) -> bool:
    if lifecycle._session_factory is None:
        return False
    settings = get_settings()
    async with lifecycle._session_factory() as session:
        user = await ensure_default_user(session, settings)
        ok = await delete_session(session, session_id=session_id, user_id=user.id)
        if ok:
            await session.commit()
        return ok


async def load_chat_history(
    session_id: uuid.UUID,
) -> tuple[list[tuple[str, str]], uuid.UUID, str] | None:
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
        return history, chat.id, display_session_title(chat.title)


_CHAT_LOADING_LABELS = (
    "Loading chats",
    "Almost done",
    "Welcome back!"
)


def prepare_chat_session_state(
    session_id: uuid.UUID | None,
    *,
    warm_voice: bool | None = None,
) -> tuple[dict, object | None, list[tuple[str, str]], str]:
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
    if worker_services() is None:
        bootstrap()
    services = worker_services()
    gateway = services.llm if services else None
    state: dict = {"session_id": session_id}
    session_label = "New Chat"
    if session_id is not None:
        loaded = run_async(load_chat_history(session_id))
        initial_history = loaded[0] if loaded else []
        if loaded:
            state["session_id"] = loaded[1]
            session_label = loaded[2]
    else:
        initial_history = []
    if warm_voice is None:
        warm_voice = term.in_tui()
    if warm_voice and voice_stt.stt_status()[0]:
        try:
            voice_stt.warm()
        except Exception:
            pass
    return state, gateway, initial_history, session_label


def start_chat_warmup() -> None:
    """Warm chat (STT, settings, LLM gateway) while the main menu is idle."""
    cfg = get_config()
    if not cfg.is_enabled("agents") or not cfg.is_enabled("retrieval"):
        return
    global _chat_warmup_thread
    with _chat_warmup_lock:
        if _chat_warmup_result is not None:
            return
        if _chat_warmup_thread is not None and _chat_warmup_thread.is_alive():
            return

        def _run_warmup() -> None:
            global _chat_warmup_result
            try:
                _chat_warmup_result = prepare_chat_session_state(None, warm_voice=True)
            except Exception:
                pass

        _chat_warmup_thread = threading.Thread(
            target=_run_warmup, name="mindos-chat-warmup", daemon=True
        )
        _chat_warmup_thread.start()


def _take_chat_warmup_result() -> tuple[dict, object | None, list[tuple[str, str]], str] | None:
    global _chat_warmup_result
    with _chat_warmup_lock:
        if _chat_warmup_result is None:
            return None
        ready = _chat_warmup_result
        _chat_warmup_result = None
        return ready


def resolve_chat_session_state(
    session_id: uuid.UUID | None,
) -> tuple[dict, object | None, list[tuple[str, str]], str]:
    if session_id is not None:
        return prepare_chat_session_state(session_id)
    ready = _take_chat_warmup_result()
    if ready is not None:
        return ready
    with _chat_warmup_lock:
        t = _chat_warmup_thread
    if t is not None and t.is_alive():

        def _wait_for_warmup() -> tuple[dict, object | None, list[tuple[str, str]], str]:
            t.join()
            hit = _take_chat_warmup_result()
            return hit if hit is not None else prepare_chat_session_state(None)

        return _wait_for_warmup()
    return prepare_chat_session_state(None)


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
    chat_owns_event_loop = not shared_event_loop_is_running()
    if chat_owns_event_loop:
        start_shared_event_loop()
    try:
        if term.in_tui():
            ready = _take_chat_warmup_result()
            if ready is not None:
                state, gateway, initial_history, session_label = ready
            else:
                with _chat_warmup_lock:
                    pending = (
                        _chat_warmup_thread is not None and _chat_warmup_thread.is_alive()
                    )
                if pending:
                    state, gateway, initial_history, session_label = term.tui_busy(
                        lambda: resolve_chat_session_state(session_id),
                        labels=_CHAT_LOADING_LABELS,
                        label_interval_s=1.5,
                    )
                else:
                    state, gateway, initial_history, session_label = (
                        resolve_chat_session_state(session_id)
                    )
        else:
            state, gateway, initial_history, session_label = resolve_chat_session_state(
                session_id
            )
    except Exception:
        if chat_owns_event_loop:
            stop_shared_event_loop()
        raise

    client_tz = detect_client_iana_timezone()

    def turn(msg: str, on_partial: Callable[[str], None] | None = None) -> str:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            ans, sid = run_async(
                fetch_chat_reply(
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
        return run_async(list_chat_sessions_for_picker())

    def load_history(sid: str) -> tuple[list[tuple[str, str]], str] | None:
        if sid == "__new__":
            state["session_id"] = None
            return [], "New Chat"
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            return None
        loaded = run_async(load_chat_history(uid))
        if loaded is None:
            return None
        hist, chat_id, title = loaded
        state["session_id"] = chat_id
        return hist, title

    def delete_history(sid: str) -> bool:
        if sid == "__new__":
            return False
        try:
            uid = uuid.UUID(sid)
        except ValueError:
            return False
        ok = run_async(delete_chat_session(uid))
        if ok and state.get("session_id") == uid:
            state["session_id"] = None
        return ok

    try:
        if term.in_tui():
            try:
                term.chat_conversation(
                    turn,
                    initial_history=initial_history,
                    initial_session_title=session_label,
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
                term.think_status(text)

        while True:
            message = term.prompt_line("ask › ")
            if not message:
                break
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", SyntaxWarning)
                    ans, sid = run_async(
                        fetch_chat_reply(
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
        if chat_owns_event_loop:
            stop_shared_event_loop()
        elif term.in_tui():
            start_chat_warmup()
