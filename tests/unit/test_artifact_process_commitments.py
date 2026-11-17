from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.worker.handlers.artifact_process import handle_artifact_process


@pytest.mark.asyncio
async def test_skips_commitment_extract_when_already_linked(monkeypatch) -> None:
    artifact_id = "11111111-1111-4111-8111-111111111111"
    chunk = MagicMock(id="22222222-2222-4222-8222-222222222222", text="todo: ship")
    artifact = MagicMock(
        id=artifact_id,
        user_id="33333333-3333-4333-8333-333333333333",
        title="t",
        external_id=None,
    )

    session = AsyncMock()
    session.get = AsyncMock(return_value=artifact)

    chunk_result = MagicMock()
    chunk_result.scalars.return_value.all.return_value = [chunk]
    commitment_hit = MagicMock()
    commitment_hit.scalar_one_or_none.return_value = "existing"
    prune = MagicMock()
    session.execute = AsyncMock(side_effect=[prune, chunk_result, commitment_hit])

    called = False

    async def _extract(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(
        "backend.worker.handlers.artifact_process.extract_commitments",
        _extract,
    )
    monkeypatch.setattr(
        "backend.worker.handlers.artifact_process.get_config",
        lambda: MagicMock(is_enabled=lambda _: False),
    )
    monkeypatch.setattr(
        "backend.worker.handlers.artifact_process.get_settings",
        lambda: MagicMock(embedding_model="m"),
    )
    monkeypatch.setattr(
        "backend.worker.handlers.artifact_process.build_provider_gateway",
        lambda _: None,
    )

    await handle_artifact_process(session, {"artifact_id": artifact_id})
    assert called is False