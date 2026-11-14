"""Route a terminal/API chat turn and persist it."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.agents.core.chat import run_chat
from backend.services.agents.core.router import classify
from backend.services.agents.core.router_llm import needs_llm_refine, refine_intent
from backend.services.agents.core.sessions import (
    append_message,
    create_session,
    get_session,
    recent_messages,
)
from backend.services.agents.core.prompt_budget import CHAT_HISTORY_TURNS
from backend.services.agents.core.turn_context import maybe_refresh_mail_index, prefetch_turn_context
from backend.services.graph.core.deep_agent import run_deep_agent


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
) -> dict:
    intent, body = classify(message)
    if needs_llm_refine(body, intent):
        refined = await refine_intent(gateway, body)
        if refined is not None:
            intent = refined
    chat = None
    if session_id is not None:
        chat = await get_session(session, session_id=session_id, user_id=user.id)
    if chat is None:
        chat = await create_session(session, user_id=user.id)

    await append_message(session, chat=chat, role="user", content=body, intent=intent)
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
            if mode == "deep_unavailable" and (turn_context.has_context or gateway is not None):
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

    return {
        "message": body,
        "answer": answer,
        "sources": sources,
        "mode": mode,
        "intent": intent,
        "session_id": str(chat.id),
        "thread_id": str(chat.thread_id),
        "title": chat.title,
    }
