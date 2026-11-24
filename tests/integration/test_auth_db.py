"""Integration states and the cross-process refresh lock on real PostgreSQL."""

from __future__ import annotations

import asyncio

import pytest

from opsagent.domain import AuthStatus
from opsagent.persistence import (
    AdvisoryRefreshLock,
    IntegrationStateRepository,
    open_pool,
    upgrade,
)

pytestmark = pytest.mark.integration


async def test_states_upsert_read_back_and_hold_no_secret(db_url: str) -> None:
    upgrade(db_url)
    pool = await open_pool(db_url, max_size=2)
    try:
        repo = IntegrationStateRepository(pool)
        assert await repo.get("google", "me@x.com") is None
        await repo.put("google", "me@x.com", AuthStatus.CONNECTED, ["openid", "email"])
        await repo.put("google", "me@x.com", AuthStatus.NEEDS_REAUTH, ["openid"])
        state = await repo.get("google", "me@x.com")
        assert state is not None
        assert (state.status, state.scopes) == (AuthStatus.NEEDS_REAUTH, ("openid",))
        await repo.put("microsoft", "me@corp.com", AuthStatus.CONNECTED, [])
        assert [s.provider for s in await repo.all()] == ["google", "microsoft"]
    finally:
        await pool.close()


async def test_the_advisory_lock_serializes_holders_of_the_same_account(db_url: str) -> None:
    upgrade(db_url)
    pool = await open_pool(db_url, max_size=6)
    try:
        lock = AdvisoryRefreshLock(pool)
        inside, overlap = 0, 0

        async def worker() -> None:
            nonlocal inside, overlap
            async with lock.hold("google", "me@x.com"):
                inside += 1
                overlap = max(overlap, inside)
                await asyncio.sleep(0.02)
                inside -= 1

        await asyncio.gather(*(worker() for _ in range(5)))
        assert overlap == 1  # never two refreshes at once

        async def other_account() -> None:
            async with lock.hold("google", "other@x.com"):
                await asyncio.sleep(0)

        async with lock.hold("google", "me@x.com"):  # a different account is not blocked
            await asyncio.wait_for(other_account(), 2)
    finally:
        await pool.close()
