import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.tensions.core.detect import detect_duplicate_commitments


@pytest.mark.asyncio
async def test_no_duplicate_returns_zero() -> None:
    session = AsyncMock()
    user_id = uuid.uuid4()
    tensions = MagicMock(all=lambda: [])
    commitments = MagicMock()
    commitments.scalars.return_value = [
        MagicMock(summary="A", status="open", source_chunk_id=None, due_at=None),
    ]
    chunks = MagicMock(all=lambda: [])
    session.execute = AsyncMock(side_effect=[tensions, commitments, chunks])
    assert await detect_duplicate_commitments(session, user_id) == 0
