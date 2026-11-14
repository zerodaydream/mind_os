import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import IntegrationAccount
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.integrations.core.gmail_sync import (
    _sync_interval_sec,
    enqueue_gmail_sync,
    schedule_gmail_sync,
    sync_gmail_inbox,
)
from backend.services.integrations.core.google_oauth import (
    SCOPES,
    auth_url,
    exchange_code,
    make_state,
    verify_state,
)
from backend.services.integrations.core.oauth_popup import oauth_popup_close_response
from backend.services.integrations.core.token_store import encrypt_tokens

router = APIRouter(tags=["integrations"])


def _ui_origin(cfg: Settings) -> str:
    return (cfg.mindos_ui_origin or "").strip().rstrip("/")


def _oauth_finish(cfg: Settings, *, popup: bool, status: str, detail: str = ""):
    origin = _ui_origin(cfg)
    if popup:
        return oauth_popup_close_response(ui_origin=origin, status=status, detail=detail)
    if status == "connected" and origin:
        return RedirectResponse(f"{origin}/?google=connected")
    if status == "connected":
        return {"status": "connected", "provider": "google"}
    raise HTTPException(status_code=400, detail=detail or status)


@router.get("/integrations/google/status")
async def google_status(
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
        )
    )
    account = result.scalar_one_or_none()
    connected = account is not None and account.status == "active"
    return {
        "provider": "google",
        "connected": connected,
        "status": account.status if account else "disconnected",
        "oauth_redirect_uri": cfg.google_oauth_redirect_uri,
    }


@router.get("/integrations/google/start")
async def google_start(
    popup: bool = Query(False),
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    if not cfg.google_oauth_client_id or not cfg.google_oauth_client_secret:
        raise HTTPException(status_code=400, detail="Set GOOGLE_OAUTH_CLIENT_ID and SECRET in .env")
    user = await ensure_default_user(session, cfg)
    await session.commit()
    state = make_state(cfg.mindos_secret_key, str(user.id), popup=popup)
    return RedirectResponse(auth_url(cfg, state=state))


@router.get("/integrations/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    parsed = verify_state(cfg.mindos_secret_key, state)
    if not parsed:
        return _oauth_finish(
            cfg,
            popup=False,
            status="error",
            detail="invalid or expired OAuth state",
        )
    popup = parsed["popup"]
    try:
        token_resp = await exchange_code(cfg, code)
    except Exception as exc:
        return _oauth_finish(cfg, popup=popup, status="error", detail=str(exc))
    tokens_enc = encrypt_tokens(cfg.mindos_secret_key, token_resp)
    uid = uuid.UUID(parsed["uid"])
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == uid,
            IntegrationAccount.provider == "google",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = IntegrationAccount(
            user_id=uid,
            provider="google",
            scopes=SCOPES,
            tokens_enc=tokens_enc,
            status="active",
        )
        session.add(account)
    else:
        account.tokens_enc = tokens_enc
        account.status = "active"
    if _sync_interval_sec(cfg) > 0:
        await enqueue_gmail_sync(
            session,
            user_id=uid,
            settings=cfg,
            available_at=datetime.now(timezone.utc),
        )
    await session.commit()
    return _oauth_finish(cfg, popup=popup, status="connected")


@router.post("/integrations/google/sync")
async def google_sync(
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    user = await ensure_default_user(session, cfg)
    try:
        stats = await sync_gmail_inbox(session, user=user, settings=cfg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await schedule_gmail_sync(session, user_id=user.id, settings=cfg)
    await session.commit()
    return stats
