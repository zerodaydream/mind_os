"""Live Gmail list+fetch for turn prefetch when hybrid search misses mail."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import Artifact, IntegrationAccount
from backend.services.ingest.core.manual import ingest_manual_note
from backend.services.integrations.core.gmail.sync import (
    _fetch_gmail_ingest_payload,
    _gmail_artifact_row,
    ensure_google_access_token,
)

_LIVE_FETCH_DEFAULT_MAX = 15


def gmail_turn_row_from_ingest_bundle(
    msg_id: str,
    bundle: dict,
    *,
    artifact_id: str | None = None,
) -> dict:
    meta = bundle.get("meta_fields") or {}
    body = (bundle.get("body") or "").strip()
    preview = ""
    if body:
        parts = body.split("\n\n", 1)
        preview = (parts[-1] if parts else body)[:500]
    return {
        "artifact_id": artifact_id or msg_id,
        "external_id": msg_id,
        "title": bundle.get("subject") or "(no subject)",
        "from_addr": meta.get("from"),
        "to_addr": (meta.get("to") or "").strip(),
        "direction": meta.get("direction"),
        "mail_at": meta.get("mail_at"),
        "preview": preview,
        "metadata": meta,
    }


async def fetch_live_gmail_turn_rows(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    query: str,
    max_messages: int = _LIVE_FETCH_DEFAULT_MAX,
    enqueue_ingest: bool = True,
) -> list[dict]:
    """List Gmail for ``query``, fetch payloads in parallel, shape like ``_ingested_gmail``."""
    q = (query or "").strip()
    if not q or max_messages <= 0:
        return []
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
            IntegrationAccount.status == "active",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        return []

    token = await ensure_google_access_token(session, account, settings)
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        nonlocal_token = {"value": token}

        async def refresh_google_auth() -> None:
            nonlocal_token["value"] = await ensure_google_access_token(
                session, account, settings, force_refresh=True
            )
            headers["Authorization"] = f"Bearer {nonlocal_token['value']}"

        async def gmail_get(url: str, *, params: dict | None = None) -> httpx.Response:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 401:
                await refresh_google_auth()
                resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp

        listed = await gmail_get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params={"maxResults": min(max_messages, 50), "q": q},
        )
        msg_ids = [m["id"] for m in listed.json().get("messages") or []][:max_messages]
        if not msg_ids:
            return []

        async def fetch_one(msg_id: str) -> tuple[str, dict | None]:
            bundle = await _fetch_gmail_ingest_payload(
                gmail_get, msg_id, show_attachment_progress=False
            )
            return msg_id, bundle

        fetched = await asyncio.gather(*(fetch_one(mid) for mid in msg_ids))

    rows: list[dict] = []
    for msg_id, bundle in fetched:
        if bundle is None:
            continue
        art = await _gmail_artifact_row(session, user.id, msg_id)
        aid = str(art.id) if art is not None else None
        row = gmail_turn_row_from_ingest_bundle(msg_id, bundle, artifact_id=aid)
        if enqueue_ingest and art is None:
            art = await ingest_manual_note(
                session,
                user=user,
                title=bundle["subject"],
                body=bundle["body"],
                settings=settings,
                source="gmail",
                external_id=msg_id,
                metadata=bundle["meta_fields"],
                occurred_at=bundle.get("occurred"),
            )
            row["artifact_id"] = str(art.id)
        rows.append(row)
    await session.flush()
    return rows