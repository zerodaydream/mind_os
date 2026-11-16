from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database import lifecycle

__all__ = ["get_session", "init_db"]

init_db = lifecycle.init_db


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session."""
    if lifecycle._session_factory is None:
        raise RuntimeError("DB not initialized; call init_db first")
    async with lifecycle._session_factory() as session:
        yield session
