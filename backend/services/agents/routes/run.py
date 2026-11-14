import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.services.graph.core.run import run_agent_pipeline
from backend.services.ingest.core.manual import ensure_default_user

router = APIRouter(tags=["agents"])


class AgentRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None


@router.post("/agents/run")
async def agents_run(
    body: AgentRunRequest,
    request: Request,
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    sid = None
    if body.session_id:
        try:
            sid = uuid.UUID(body.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid session_id") from exc
    user = await ensure_default_user(session, cfg)
    gateway = getattr(request.app.state.services, "llm", None)
    return await run_agent_pipeline(
        session, user, cfg, gateway, body.prompt, session_id=sid
    )
