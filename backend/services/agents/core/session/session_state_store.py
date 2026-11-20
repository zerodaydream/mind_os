"""Load and save ChatSessionStateRow for a chat session."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import ChatSessionStateRow
from backend.services.agents.core.session.session_state import ChatSessionState


async def load_chat_session_state(
    session: AsyncSession, session_id: uuid.UUID
) -> ChatSessionState:
    """Return structured state for a chat, or empty defaults."""
    row = await session.get(ChatSessionStateRow, session_id)
    if row is None:
        return ChatSessionState()
    return ChatSessionState.from_json(row.state or {})


async def save_chat_session_state(
    session: AsyncSession,
    session_id: uuid.UUID,
    state: ChatSessionState,
) -> None:
    """Persist structured session state (insert or update)."""
    row = await session.get(ChatSessionStateRow, session_id)
    payload = state.to_json()
    if row is None:
        session.add(ChatSessionStateRow(session_id=session_id, state=payload))
    else:
        row.state = payload
