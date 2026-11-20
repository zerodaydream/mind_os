"""Write turn_log rows for turn observability."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import TurnLog


async def record_turn_log(
    session: AsyncSession,
    *,
    session_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    tier: int | None = None,
    mode: str | None = None,
    timings_ms: dict[str, Any] | None = None,
    token_counts: dict[str, Any] | None = None,
    evidence_eids: list[str] | None = None,
    rerank_margin: float | None = None,
    verifier_ok: bool | None = None,
    verifier_violations: list[str] | None = None,
    spend_usd: float | None = None,
) -> TurnLog:
    """Insert one turn_log row; caller commits with the chat transaction."""
    row = TurnLog(
        session_id=session_id,
        user_id=user_id,
        tier=tier,
        mode=mode,
        timings_ms=timings_ms or {},
        token_counts=token_counts or {},
        evidence_eids=evidence_eids or [],
        rerank_margin=rerank_margin,
        verifier_ok=verifier_ok,
        verifier_violations=verifier_violations or [],
        spend_usd=spend_usd,
    )
    session.add(row)
    return row
