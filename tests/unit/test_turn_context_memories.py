import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.agents.core.turn.turn_context import prefetch_turn_context


@pytest.mark.asyncio
async def test_plan_uses_memory_search_when_hits(monkeypatch):
    user = MagicMock(id=uuid.uuid4())
    settings = MagicMock()

    async def fake_hybrid(*_a, **_k):
        return []

    async def fake_mem_search(session, *, user_id, query, limit):
        return [{"kind": "pref", "content": "likes tea", "rank": 1.0}]

    async def fake_approved(*_a, **_k):
        pytest.fail("should not fall back when search hits")

    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_context.hybrid_search_artifacts",
        fake_hybrid,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_context.search_memories_ilike",
        fake_mem_search,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_context._approved_memories",
        fake_approved,
    )

    session = AsyncMock()
    ctx = await prefetch_turn_context(
        session, user, settings, "schedule meeting tomorrow", intent="plan"
    )
    assert ctx.memories == ["[pref] likes tea"]