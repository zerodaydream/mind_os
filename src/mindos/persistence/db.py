"""PostgreSQL connection pool and URL helpers."""

from __future__ import annotations

from typing import cast

from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

Pool = AsyncConnectionPool[AsyncConnection[DictRow]]


def sqlalchemy_url(url: str) -> str:
    """Alembic runs through SQLAlchemy; pick the psycopg 3 driver explicitly."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


async def open_pool(url: str, *, max_size: int = 10) -> Pool:
    """Open an async pool whose rows are dicts. The caller must `await pool.close()`."""
    pool = cast(
        Pool,
        AsyncConnectionPool(
            url,
            min_size=1,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        ),
    )
    await pool.open(wait=True, timeout=10)
    return pool
