from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.services.ingest.core.manual import ensure_default_user, ingest_manual_note

router = APIRouter(tags=["ingest"])


class ManualIngestRequest(BaseModel):
    title: str | None = None
    body: str = Field(..., min_length=1)


class ManualIngestResponse(BaseModel):
    artifact_id: str
    status: str = "queued"


@router.post("/ingest/manual", response_model=ManualIngestResponse)
async def ingest_manual(
    req: ManualIngestRequest,
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
) -> ManualIngestResponse:
    user = await ensure_default_user(session, cfg)
    artifact = await ingest_manual_note(
        session, user=user, title=req.title, body=req.body, settings=cfg
    )
    await session.commit()
    return ManualIngestResponse(artifact_id=str(artifact.id))
