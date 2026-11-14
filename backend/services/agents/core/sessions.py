"""Chat session persistence for terminal + agent threads."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.common.database.models import ChatMessage, ChatSession

_TITLE_MAX = 60


def _title_from(message: str) -> str:
    text = " ".join(message.strip().split())
    if len(text) <= _TITLE_MAX:
        return text or "New chat"
    return text[: _TITLE_MAX - 1] + "…"


async def create_session(session: AsyncSession, *, user_id: uuid.UUID) -> ChatSession:
    row = ChatSession(user_id=user_id, title="New chat", thread_id=uuid.uuid4())
    session.add(row)
    await session.flush()
    return row


async def get_session(
    session: AsyncSession, *, session_id: uuid.UUID, user_id: uuid.UUID
) -> ChatSession | None:
    row = await session.execute(
        select(ChatSession)
        .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
        .options(selectinload(ChatSession.messages))
    )
    return row.scalar_one_or_none()


async def list_sessions(
    session: AsyncSession, *, user_id: uuid.UUID, limit: int = 50
) -> list[dict]:
    msg_count = (
        select(func.count(ChatMessage.id))
        .where(ChatMessage.session_id == ChatSession.id)
        .correlate(ChatSession)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(ChatSession, msg_count.label("n_msgs"))
        .where(ChatSession.user_id == user_id)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    out: list[dict] = []
    for s, n in rows.all():
        out.append(
            {
                "id": s.id,
                "title": s.title,
                "thread_id": s.thread_id,
                "n_msgs": int(n or 0),
                "updated_at": s.updated_at,
                "created_at": s.created_at,
            }
        )
    return out


async def append_message(
    session: AsyncSession,
    *,
    chat: ChatSession,
    role: str,
    content: str,
    intent: str | None = None,
    metadata: dict | None = None,
) -> ChatMessage:
    msg = ChatMessage(
        session_id=chat.id,
        role=role,
        content=content,
        intent=intent,
        metadata_=metadata or {},
    )
    session.add(msg)
    if role == "user" and (not chat.title or chat.title == "New chat"):
        chat.title = _title_from(content)
    chat.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return msg


async def recent_messages(
    session: AsyncSession, *, session_id: uuid.UUID, limit: int = 12
) -> list[dict]:
    rows = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )
    msgs = list(reversed(rows.scalars().all()))
    return [{"role": m.role, "content": m.content, "intent": m.intent} for m in msgs]
