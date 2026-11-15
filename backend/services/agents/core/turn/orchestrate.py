"""Route a terminal/API chat turn and persist it."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.agents.core.converse.chat import run_chat
from backend.services.agents.core.routing.router import classify, needs_url_agent
from backend.services.agents.core.routing.router_llm import (
    _looks_like_thread_continuation,
    compact_thread_task,
    needs_turn_routing,
    route_turn,
    thread_pending_execute,
)
from backend.services.agents.core.session.sessions import (
    append_message,
    create_session,
    get_session,
    recent_messages,
)
from backend.services.agents.core.render.prompt_budget import CHAT_HISTORY_TURNS
from backend.services.agents.core.turn.turn_context import maybe_refresh_mail_index, prefetch_turn_context
from backend.common.langsmith_setup import begin_turn_trace, flush_traces, tracing_active, unique_run_name
from backend.services.integrations.core.calendar.scheduling import pending_calendar_slot_reply
from backend.services.graph.core.deep_agent import run_deep_agent, should_route_deep_for_oauth


def _mail_table_ask(message: str) -> bool:
    lower = message.lower()
    if not any(w in lower for w in ("gmail", "email", "mail", "inbox")):
        return False
    return any(
        w in lower
        for w in ("find", "list", "show", "all", "table", "tabular", "subject", "role")
    )


async def _synth_plan(
    session,
    user,
    settings,
    gateway,
    body,
    *,
    source,
    history,
    turn_context,
    on_partial: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    out = await run_chat(
        session,
        user,
        settings,
        gateway,
        body,
        source=source,
        history=history,
        turn_context=turn_context,
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
        if ls_cm is not None:
            ls_cm.__exit__(None, None, None)


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
    intent, body = classify(message)
    if intent == "converse" and needs_url_agent(body, prior_history):
        intent = "plan"
    if should_route_deep_for_oauth(body, prior_history):
        intent = "execute"
    if intent == "converse" and pending_calendar_slot_reply(body, prior_history):
        intent = "execute"
        body = compact_thread_task(body, prior_history)
    if needs_turn_routing(body, intent, prior_history):
        routed = await route_turn(gateway, message, prior_history)
        if routed is not None:
            intent, body = routed
        elif thread_pending_execute(prior_history) and _looks_like_thread_continuation(message):
            intent = "execute"
            body = compact_thread_task(message, prior_history)

    user_line = message.strip()
    await append_message(session, chat=chat, role="user", content=user_line, intent=intent)
    history = await recent_messages(session, session_id=chat.id, limit=CHAT_HISTORY_TURNS)
    # drop the message we just appended from history fed into the model
    history = history[:-1] if history and history[-1].get("role") == "user" else history

    turn_context = await prefetch_turn_context(
        session, user, settings, body, source=source, intent=intent
    )
    turn_context = await maybe_refresh_mail_index(
        session,
        user,
        settings,
        body,
        turn_context,
        source=source,
        intent=intent,
        gateway=gateway,
        on_partial=on_partial,
    )

    if intent in ("plan", "execute") and _mail_table_ask(body):
        out, mode = await _synth_plan(
            session, user, settings, gateway, body,
            source=source, history=history, turn_context=turn_context,
            on_partial=on_partial,
        )
        answer = out.get("answer") or ""
        sources = out.get("sources") or turn_context.sources
    elif intent in ("plan", "execute"):
        try:
            out = await run_deep_agent(
                session,
                user,
                settings,
                gateway,
                body,
                intent=intent,
                thread_id=chat.thread_id,
                turn_context=turn_context,
                history=history,
                on_partial=on_partial,
                client_tz=client_tz,
            )
        except OSError as exc:
            out, mode = await _synth_plan(
                session, user, settings, gateway, body,
                source=source, history=history, turn_context=turn_context,
                on_partial=on_partial,
            )
            answer = out.get("answer") or ""
            if answer:
                answer = f"{answer}\n\n_(deep agent offline: {exc})_"
            sources = out.get("sources") or turn_context.sources
        else:
            mode = out.get("mode") or "deep_agent"
            # execute needs tools (calendar, files) — synth chat cannot call APIs
            if (
                mode == "deep_unavailable"
                and intent == "plan"
                and (turn_context.has_context or gateway is not None)
            ):
                out, mode = await _synth_plan(
                    session, user, settings, gateway, body,
                    source=source, history=history, turn_context=turn_context,
                    on_partial=on_partial,
                )
            answer = out.get("answer") or ""
            sources = out.get("sources") or turn_context.sources
    else:
        out = await run_chat(
            session,
            user,
            settings,
            gateway,
            body,
            source=source,
            history=history,
            turn_context=turn_context,
            on_partial=on_partial,
        )
        answer = out.get("answer") or ""
        mode = out.get("mode") or "synth"
        sources = out.get("sources") or []

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
        "title": chat.title,
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
