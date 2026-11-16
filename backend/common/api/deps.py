from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings, get_settings
from backend.common.database.session import get_session
from backend.services.llm.core.gateway import ProviderGateway


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get a database session."""
    async for session in get_session():
        yield session


def app_settings() -> Settings:
    """Dependency to get the application settings."""
    return get_settings()


def require_integrations(request: Request) -> None:
    """Dependency to check if integrations are enabled."""
    if not request.app.state.config.is_enabled("integrations"):
        raise HTTPException(status_code=503, detail="integrations disabled in config.json")


def llm_gateway(request: Request) -> ProviderGateway:
    """Dependency to get the LLM gateway."""
    gw = request.app.state.services.llm
    if gw is None:
        raise HTTPException(status_code=503, detail="LLM service disabled in config.json")
    return gw
