import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.agents.core.turn.turn_retrieval import (
    _attachment_filename_needle,
    _attachment_named_in_gmail_ctx,
    prefetch_turn_retrieval,
)


def test_attachment_filename_needle() -> None:
    assert _attachment_filename_needle("summarize Propico deck.pdf from mail") == "Propico deck.pdf"


def test_attachment_named_in_ctx() -> None:
    rows = [{"metadata": {"attachments": [{"filename": "deck.pdf"}]}}]
    assert _attachment_named_in_gmail_ctx(rows, "deck.pdf")
    assert not _attachment_named_in_gmail_ctx(rows, "other.pdf")


@pytest.mark.asyncio
async def test_hybrid_non_gmail_hits_still_load_gmail_ctx(monkeypatch):
    user = MagicMock(id=uuid.uuid4())
    settings = MagicMock()
    aid = uuid.uuid4()

    async def fake_hybrid(*_a, **_k):
        return [
            {
                "artifact_id": str(aid),
                "title": "notes",
                "source": "manual",
                "rank": 0.9,
            }
        ]

    async def fake_lookup(session, user_id, settings, query, **kwargs):
        return [
            {
                "artifact_id": str(uuid.uuid4()),
                "title": "Propico",
                "from_addr": "a@b.co",
                "to_addr": "",
                "direction": "received",
                "mail_at": "",
                "body": "see deck.pdf",
                "preview": "see deck.pdf",
                "metadata": {"attachments": [{"filename": "deck.pdf"}]},
            }
        ]

    async def fake_peek(*_a, **_k):
        pytest.fail("peek should not run when ingested rows exist")

    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.hybrid_search_artifacts",
        fake_hybrid,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.lookup_indexed_mail",
        fake_lookup,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.fetch_live_gmail_turn_rows",
        fake_peek,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval._open_commitment_summaries",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.search_memories_ilike",
        AsyncMock(return_value=[]),
    )
    session = AsyncMock()
    ctx = await prefetch_turn_retrieval(
        session,
        user,
        settings,
        "deck.pdf from Propico mail",
        prep=MagicMock(
            skip_retrieval=False,
            prefetch_mail=True,
            prefetch_commitments=False,
            prefetch_memories=False,
            search_query="deck.pdf Propico",
        ),
    )
    assert ctx.gmail_ctx
    assert "deck.pdf" in ctx.context_text
    assert any("Prefetched" in a or "Indexed search" in a for a in ctx.preflight_actions)


@pytest.mark.asyncio
async def test_live_gmail_preflight_and_hint(monkeypatch):
    from backend.services.agents.core.turn.turn_retrieval import preloaded_mail_agent_hint

    user = MagicMock(id=uuid.uuid4())
    settings = MagicMock()

    async def fake_hybrid(*_a, **_k):
        return []

    async def fake_lookup(*_a, **_k):
        return []

    async def fake_live(*_a, **_k):
        return [
            {
                "artifact_id": "msg123",
                "external_id": "msg123",
                "title": "Propico",
                "from_addr": "a@b.co",
                "to_addr": "",
                "direction": "received",
                "mail_at": "",
                "body": "see deck",
                "preview": "see deck",
                "metadata": {},
            }
        ]

    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.hybrid_search_artifacts",
        fake_hybrid,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.lookup_indexed_mail",
        fake_lookup,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.fetch_live_gmail_turn_rows",
        fake_live,
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval._open_commitment_summaries",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval.search_memories_ilike",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "backend.services.agents.core.turn.turn_retrieval._ingested_gmail",
        AsyncMock(return_value=[]),
    )
    session = AsyncMock()
    ctx = await prefetch_turn_retrieval(
        session,
        user,
        settings,
        "deck.pdf from Propico mail",
        prep=MagicMock(
            skip_retrieval=False,
            prefetch_mail=True,
            prefetch_commitments=False,
            prefetch_memories=False,
            search_query="deck.pdf Propico",
        ),
    )
    assert ctx.gmail_ctx
    assert any("Live Gmail preview" in a for a in ctx.preflight_actions)
    assert "live" in preloaded_mail_agent_hint(ctx).lower()