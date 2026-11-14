import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import Tension
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["tensions"])


class TensionPatch(BaseModel):
    status: str = Field(..., pattern="^(resolved|dismissed)$")


@router.patch("/tensions/{tension_id}")
async def patch_tension(
    tension_id: uuid.UUID,
    body: TensionPatch,
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    tension = await session.get(Tension, tension_id)
    if tension is None or tension.user_id != user.id:
        raise HTTPException(status_code=404, detail="tension not found")
    if tension.status != "open":
        raise HTTPException(status_code=409, detail="tension already closed")
    tension.status = body.status
    tension.resolution = {"by": "user", "status": body.status}
    await session.commit()
    return {"id": str(tension.id), "status": tension.status}
