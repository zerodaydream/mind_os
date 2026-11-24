"""`integration_states` and the cross-process refresh lock. No secret is stored here."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from opsagent.domain import AuthStatus, IntegrationState
from opsagent.persistence.db import Pool


class IntegrationStateRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def get(self, provider: str, account_id: str) -> IntegrationState | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM integration_states WHERE provider = %s AND account_id = %s",
                (provider, account_id),
            )
            row = await cur.fetchone()
        return None if row is None else _state(dict(row))

    async def put(
        self, provider: str, account_id: str, status: AuthStatus, scopes: Sequence[str]
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO integration_states (provider, account_id, status, scopes)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (provider, account_id)
                DO UPDATE SET status = EXCLUDED.status, scopes = EXCLUDED.scopes,
                              updated_at = now()
                """,
                (provider, account_id, status.value, list(scopes)),
            )

    async def all(self) -> list[IntegrationState]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM integration_states ORDER BY provider, account_id"
            )
            rows = await cur.fetchall()
        return [_state(dict(r)) for r in rows]


class AdvisoryRefreshLock:
    """A per-account advisory lock scoped to a transaction: a crash cannot leave it held."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    @asynccontextmanager
    async def hold(self, provider: str, account_id: str) -> AsyncIterator[None]:
        async with self._pool.connection() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"opsagent.refresh:{provider}:{account_id}",),
            )
            yield


def _state(row: dict[str, Any]) -> IntegrationState:
    return IntegrationState(
        provider=row["provider"],
        account_id=row["account_id"],
        status=AuthStatus(row["status"]),
        scopes=tuple(row["scopes"]),
        updated_at=row["updated_at"],
    )
