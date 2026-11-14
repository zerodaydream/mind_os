from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import Memory
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["memories"])


@router.get("/memories")
async def list_memories(
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    rows = await session.execute(
        select(Memory).where(Memory.user_id == user.id, Memory.approved.is_(True)).order_by(Memory.updated_at.desc())
    )
    return {
        "items": [
            {
                "id": str(m.id),
                "kind": m.kind,
                "content": m.content,
                "source": m.source,
            }
            for m in rows.scalars()
        ]
    }
