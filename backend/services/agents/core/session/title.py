"""LLM session title from the first user message (runs parallel to the turn)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.render.partial import emit_partial
from backend.common.database.models import ChatSession
from backend.services.agents.core.session.sessions import _title_from

_TITLE_PROMPT = (
    "Write a short chat session title (3-8 words) for this user message. "
    "Title case. No quotes. No period.\n\nMessage:\n{msg}\n\nTitle:"
)


def display_session_title(title: str | None) -> str:
    t = " ".join((title or "").split())
    if not t or t.lower() == "new chat":
        return "New Chat"
    return t


def needs_session_title(title: str | None) -> bool:
    t = (title or "").strip().lower()
    return not t or t == "new chat"


async def generate_session_title(
    gateway,
    message: str,
    chat: ChatSession,
    session: AsyncSession,
    *,
    on_partial: Callable[..., None] | None = None,
) -> str:
    fallback = _title_from(message)
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        chat.title = fallback
        await session.flush()
        emit_partial(on_partial, display_session_title(fallback), phase="title")
        return fallback
    prompt = _TITLE_PROMPT.format(msg=message[:800])
    try:
        res = await gateway.complete(
            prompt, task=LlmTask.EXTRACT, max_output_tokens=32
        )
        raw = (res.text or "").strip().strip("\"'").splitlines()[0].strip()
        title = " ".join(raw.split()) if raw else fallback
    except Exception:
        title = fallback
    if not title:
        title = fallback
    chat.title = title
    await session.flush()
    emit_partial(on_partial, display_session_title(title), phase="title")
    return title