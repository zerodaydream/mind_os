from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import Tension
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["tensions"])


@router.get("/tensions")
async def list_tensions(
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    rows = await session.execute(
        select(Tension).where(Tension.user_id == user.id, Tension.status == "open")
    )
    return {
        "items": [
            {
                "id": str(t.id),
                "tension_type": t.tension_type,
                "severity": t.severity,
                "description": t.description,
                "artifact_ids": [str(a) for a in t.artifact_ids],
            }
            for t in rows.scalars()
        ]
    }
