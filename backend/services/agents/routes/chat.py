from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
import uuid

from backend.services.agents.core.turn.orchestrate import handle_turn
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.integrations.core.calendar.timezone import detect_client_iana_timezone

router = APIRouter(tags=["agents"])

_ALLOWED_SOURCES = frozenset({"gmail", "manual"})


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    source: Literal["gmail", "manual"] | None = None
    session_id: str | None = None
    client_tz: str | None = None

    @field_validator("message")
    @classmethod
    def _strip_message(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("message cannot be blank")
        return stripped


@router.post("/chat")
async def chat(
    body: ChatRequest,
    request: Request,
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    if not request.app.state.config.is_enabled("agents"):
        raise HTTPException(status_code=503, detail="agents disabled in config.json")
    if not request.app.state.config.is_enabled("retrieval"):
        raise HTTPException(status_code=503, detail="retrieval disabled in config.json")
    if body.source and body.source not in _ALLOWED_SOURCES:
        raise HTTPException(status_code=400, detail="invalid source filter")
    sid = None
    if body.session_id:
        try:
            sid = uuid.UUID(body.session_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid session_id") from exc
    user = await ensure_default_user(session, cfg)
    gateway = getattr(request.app.state.services, "llm", None)
    client_tz = (body.client_tz or "").strip() or detect_client_iana_timezone()
    return await handle_turn(
        session,
        user,
        cfg,
        gateway,
        body.message,
        session_id=sid,
        source=body.source,
        client_tz=client_tz,
    )
