import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.api.deps import app_settings, db_session
from backend.common.config_loader.settings import Settings
from backend.common.database.models import IntegrationAccount
from backend.services.ingest.core.manual import ensure_default_user
from backend.services.integrations.core.gmail.sync import (
    _sync_interval_sec,
    enqueue_gmail_sync,
    schedule_gmail_sync,
    sync_gmail_inbox,
)
from backend.services.integrations.core.google.oauth import (
    SCOPES,
    auth_url,
    exchange_code,
    make_state,
    scopes_from_token_response,
    verify_state,
)
from backend.services.integrations.core.google.oauth_popup import (
    oauth_done_page,
    oauth_popup_close_response,
)
from backend.services.integrations.core.google.token_store import encrypt_tokens

router = APIRouter(tags=["integrations"])


def _ui_origin(cfg: Settings) -> str:
    return (cfg.mindos_ui_origin or "").strip().rstrip("/")


def _google_done_url(cfg: Settings, *, status: str, detail: str = "") -> str:
    base = cfg.google_oauth_redirect_uri.rsplit("/callback", 1)[0]
    url = f"{base}/done?status={quote(status, safe='')}"
    if detail:
        url += f"&detail={quote(detail, safe='')}"
    return url


def _oauth_finish(
    cfg: Settings,
    *,
    popup: bool,
    status: str,
    detail: str = "",
):
    origin = _ui_origin(cfg)
    if popup:
        return oauth_popup_close_response(
            ui_origin=origin or "http://127.0.0.1",
            status=status,
            detail=detail,
        )
    if origin:
        if status == "connected":
            return RedirectResponse(f"{origin}/?google=connected")
        return RedirectResponse(
            f"{origin}/?google=error&detail={quote(detail, safe='')}"
        )
    return RedirectResponse(_google_done_url(cfg, status=status, detail=detail))


@router.get("/integrations/google/done")
async def google_oauth_done(
    status: str = Query("connected"),
    detail: str = Query(""),
    session: AsyncSession = Depends(db_session),
    cfg: Settings = Depends(app_settings),
):
    granted: list[str] = []
    user = await ensure_default_user(session, cfg)
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
        )
    )
    account = result.scalar_one_or_none()
    if account is not None:
        granted = list(account.scopes or [])
    return oauth_done_page(status=status, detail=detail, granted_scopes=granted)


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
    granted = list(account.scopes or []) if account else []
    return {
        "provider": "google",
        "connected": connected,
        "status": account.status if account else "disconnected",
        "granted_scopes": granted,
        "calendar_granted": any("calendar" in s for s in granted),
        "requested_scopes": list(SCOPES),
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
    granted_scopes = scopes_from_token_response(token_resp)
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
            scopes=granted_scopes,
            tokens_enc=tokens_enc,
            status="active",
        )
        session.add(account)
    else:
        account.tokens_enc = tokens_enc
        account.scopes = granted_scopes
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
