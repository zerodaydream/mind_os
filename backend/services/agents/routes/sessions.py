import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.services.agents.core.session.sessions import delete_session
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["agents"])


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(
    session_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    if not request.app.state.config.is_enabled("agents"):
        raise HTTPException(status_code=503, detail="agents disabled in config.json")
    user = await ensure_default_user(session, cfg)
    if not await delete_session(session, session_id=session_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="session not found")
    await session.commit()
    return {"deleted": True, "session_id": str(session_id)}