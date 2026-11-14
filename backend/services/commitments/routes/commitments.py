from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import Commitment
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["commitments"])


@router.get("/commitments")
async def list_commitments(
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    rows = await session.execute(
        select(Commitment).where(Commitment.user_id == user.id, Commitment.status == "open")
    )
    items = rows.scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "summary": c.summary,
                "confidence": c.confidence,
                "extraction_mode": c.extraction_mode,
            }
            for c in items
        ]
    }
