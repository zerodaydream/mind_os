from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.services.retrieval.core.search_memories import search_memories_ilike

router = APIRouter(tags=["retrieval"])


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    body = {
        "query": q,
        "results": await hybrid_search_artifacts(session, user_id=user.id, query=q, settings=cfg),
    }
    if request.app.state.config.is_enabled("memories"):
        body["memories"] = await search_memories_ilike(session, user_id=user.id, query=q)
    return body
