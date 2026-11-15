import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import get_settings
from backend.common.database.models import User
from backend.services.integrations.core.gmail.sync import schedule_gmail_sync, sync_gmail_inbox


async def handle_gmail_sync(session: AsyncSession, payload: dict) -> None:
    user = await session.get(User, uuid.UUID(payload["user_id"]))
    if user is None:
        return
    settings = get_settings()
    await sync_gmail_inbox(session, user=user, settings=settings)
    await schedule_gmail_sync(session, user_id=user.id, settings=settings)
