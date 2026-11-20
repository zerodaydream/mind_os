"""Route a terminal/API chat turn and persist it."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.agents.core.converse.converse_synth import run_converse_turn
from backend.services.agents.core.routing.router import (
    thread_pending_execute,
    url_from_thread,
)
from dataclasses import replace

from backend.services.agents.core.routing.turn_prep import (
    TurnPrep,
    has_execute_verbs,
    is_short_confirm,
    is_vague_thread_followup,
    prepare_turn,
)
from backend.services.agents.core.turn.turn_retrieval import (
    TurnRetrieval,
    looks_like_named_attachment,
    prefetch_turn_retrieval,
    scope_block_reply,
    turn_in_scope,
)
from backend.services.agents.core.session.sessions import (
    append_message,
    create_session,
    get_session,
    recent_messages,
)
from backend.services.agents.core.session.title import (
    display_session_title,
    generate_session_title,
    needs_session_title,
)
from backend.services.agents.core.render.chat_stream import emit_chat_stream_reveal
from backend.services.agents.core.render.prompt_budget import CHAT_HISTORY_TURNS
from backend.common.langsmith_setup import begin_turn_trace, flush_traces, tracing_active, unique_run_name
from backend.services.graph.core.deep_agent import run_deep_agent


def needs_deep_agent(
    prep: TurnPrep,
    task: str,
    history: list[dict] | None,
    turn_retrieval: TurnRetrieval | None = None,
) -> bool:
    """True when calendar/fs/tools or a pending execute thread need the deep agent."""
    if prep.intent == "execute":
        return True
    if (task or "").strip().startswith("/"):
        return True
    if is_short_confirm(task) and thread_pending_execute(history):
        return True
    if has_execute_verbs(task):
        return True
    if looks_like_named_attachment(task) and (
        turn_retrieval is None or not turn_retrieval.has_context
    ):
        return True
    return False


async def _finish_out_of_scope(
    session,
    chat,
    *,
    user_line: str,
    intent: str,
    prep,
    title_task: asyncio.Task | None,
    on_partial: Callable[[str], None] | None,
) -> dict:
    answer = scope_block_reply(
        user_line, block=prep.scope_block, clarify=prep.clarify_reply
    )
    mode = "out_of_scope" if prep.scope_block == "sorry" else "clarify"
    emit_chat_stream_reveal(on_partial, answer)
    if title_task is not None:
        with contextlib.suppress(Exception):
            await title_task
    await append_message(
        session,
        chat=chat,
        role="assistant",
        content=answer,
        intent=intent,
        metadata={"mode": mode, "sources": []},
    )
    return {
        "message": user_line,
        "answer": answer,
        "sources": [],
        "mode": mode,
        "intent": intent,
        "session_id": str(chat.id),
        "thread_id": str(chat.thread_id),
        "title": display_session_title(chat.title),
    }


async def _synth_plan(
    session,
    user,
    settings,
    gateway,
    body,
    *,
    source,
    history,
    turn_retrieval,
    prep,
    on_partial: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    """Converse/plan synth fallback when deep agent is unavailable."""
    out = await run_converse_turn(
        session,
        user,
        settings,
        gateway,
        body,
        source=source,
        history=history,
        turn_retrieval=turn_retrieval,
        prep=prep,
        plan_style=True,
        on_partial=on_partial,
    )
    return out, "plan_synth"


async def handle_turn(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    session_id: uuid.UUID | None = None,
    source: str | None = None,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
) -> dict:
    turn_trace = begin_turn_trace() if tracing_active() else None
    ls_cm = None
    ls_run = None
    if tracing_active():
        from langsmith.run_helpers import trace

        ls_cm = trace(
            name=unique_run_name("mindos_handle_turn"),
            run_type="chain",
            inputs={
                "message": message[:4000],
                "session_id": str(session_id) if session_id else None,
                "user_id": str(getattr(user, "id", "")),
                "turn_trace_id": turn_trace,
            },
            metadata={
                "thread_id": str(session_id) if session_id else None,
                "mindos_turn_trace_id": turn_trace,
            },
        )
        ls_run = ls_cm.__enter__()
    try:
        return await _handle_turn_body(
            session,
            user,
            settings,
            gateway,
            message,
            session_id=session_id,
            source=source,
            on_partial=on_partial,
            client_tz=client_tz,
            ls_run=ls_run,
        )
    except Exception as exc:
        if ls_run is not None:
            ls_run.end(error=str(exc))
        raise
    finally:
        exc_info = sys.exc_info()
        if ls_run is not None and exc_info[0] is KeyboardInterrupt:
            try:
                ls_run.end(error="cancelled")
            except Exception:
                pass
        if ls_cm is not None:
            ls_cm.__exit__(*exc_info)
        if exc_info[0] is not None:
            flush_traces()


async def _handle_turn_body(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    session_id: uuid.UUID | None = None,
    source: str | None = None,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
    ls_run=None,
) -> dict:
    chat = None
    if session_id is not None:
        chat = await get_session(session, session_id=session_id, user_id=user.id)
    if chat is None:
        chat = await create_session(session, user_id=user.id)

    prior_history = await recent_messages(session, session_id=chat.id, limit=CHAT_HISTORY_TURNS)
    user_line = message.strip()
    prep = await prepare_turn(gateway, user_line, prior_history)
    intent, body = prep.intent, prep.task
    source_url = url_from_thread(body, prior_history)
    if source_url and source_url not in body:
        body = f"{body.strip()}\n\nSource URL: {source_url}"

    title_task: asyncio.Task | None = None
    if needs_session_title(chat.title) and not prior_history:
        title_task = asyncio.create_task(
            generate_session_title(
                gateway, user_line, chat, session, on_partial=on_partial
            )
        )
    await append_message(session, chat=chat, role="user", content=user_line, intent=intent)
    history = await recent_messages(session, session_id=chat.id, limit=CHAT_HISTORY_TURNS)
    # drop the message we just appended from history fed into the model
    history = history[:-1] if history and history[-1].get("role") == "user" else history

    vague_followup = is_vague_thread_followup(user_line, history)
    if prep.blocks_chat_turn() and not vague_followup:
        return await _finish_out_of_scope(
            session,
            chat,
            user_line=user_line,
            intent=intent,
            prep=prep,
            title_task=title_task,
            on_partial=on_partial,
        )

    prep_fetch = prep
    if vague_followup and not prep.in_scope:
        prep_fetch = replace(
            prep,
            prefetch_memories=True,
            prefetch_mail=prep.prefetch_mail or True,
        )

    turn_retrieval = await prefetch_turn_retrieval(
        session,
        user,
        settings,
        body,
        source=source,
        intent=intent,
        history=history,
        prep=prep_fetch,
    )

    if prep.blocks_chat_turn() and not turn_in_scope(
        prep, body, history=history, turn_retrieval=turn_retrieval
    ):
        return await _finish_out_of_scope(
            session,
            chat,
            user_line=user_line,
            intent=intent,
            prep=prep,
            title_task=title_task,
            on_partial=on_partial,
        )

    use_deep = needs_deep_agent(prep, body, history, turn_retrieval)
    if not use_deep:
        out = await run_converse_turn(
            session,
            user,
            settings,
            gateway,
            body,
            source=source,
            history=history,
            turn_retrieval=turn_retrieval,
            prep=prep,
            plan_style=(prep.intent == "plan"),
            on_partial=on_partial,
        )
        mode = "plan_synth" if prep.intent == "plan" else (out.get("mode") or "converse")
        answer = out.get("answer") or ""
        raw_sources = out.get("sources")
        sources = list(raw_sources) if raw_sources is not None else []
    else:
        try:
            out = await run_deep_agent(
                session,
                user,
                settings,
                gateway,
                body,
                intent=intent,
                thread_id=chat.thread_id,
                turn_retrieval=turn_retrieval,
                history=history,
                on_partial=on_partial,
                client_tz=client_tz,
            )
        except asyncio.CancelledError:
            if title_task is not None:
                title_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await title_task
            return {
                "message": user_line,
                "answer": "",
                "sources": [],
                "mode": "cancelled",
                "intent": intent,
                "session_id": str(chat.id),
                "thread_id": str(chat.thread_id),
                "title": display_session_title(chat.title),
            }
        except OSError as exc:
            out, mode = await _synth_plan(
                session, user, settings, gateway, body,
                source=source, history=history, turn_retrieval=turn_retrieval,
                prep=prep,
                on_partial=on_partial,
            )
            answer = out.get("answer") or ""
            if answer:
                answer = f"{answer}\n\n_(deep agent offline: {exc})_"
            raw_sources = out.get("sources")
            sources = list(raw_sources) if raw_sources is not None else []
        else:
            mode = out.get("mode") or "deep_agent"
            if (
                mode == "deep_unavailable"
                and (turn_retrieval.has_context or gateway is not None)
            ):
                out, mode = await _synth_plan(
                    session, user, settings, gateway, body,
                    source=source, history=history, turn_retrieval=turn_retrieval,
                    prep=prep,
                    on_partial=on_partial,
                )
            answer = out.get("answer") or ""
            raw_sources = out.get("sources")
            sources = list(raw_sources) if raw_sources is not None else []

    if title_task is not None:
        try:
            await title_task
        except Exception:
            pass

    await append_message(
        session,
        chat=chat,
        role="assistant",
        content=answer,
        intent=intent,
        metadata={"mode": mode, "sources": sources},
    )

    result = {
        "message": user_line,
        "answer": answer,
        "sources": sources,
        "mode": mode,
        "intent": intent,
        "session_id": str(chat.id),
        "thread_id": str(chat.thread_id),
        "title": display_session_title(chat.title),
    }
    if ls_run is not None:
        ls_run.end(
            outputs={
                "intent": intent,
                "mode": mode,
                "answer": (answer or "")[:8000],
                "thread_id": str(chat.thread_id),
                "session_id": str(chat.id),
            },
            metadata={"routing": "deep_agent" if mode == "deep_agent" else "synth"},
        )
    flush_traces()
    return result
