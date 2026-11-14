import base64
import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import Artifact, IntegrationAccount, OutboxEvent
from backend.common.database.outbox import enqueue
from backend.services.ingest.core.manual import ingest_manual_note
from backend.services.integrations.core.google_oauth import (
    access_token_expired,
    refresh_access_token,
)
from backend.services.integrations.core.token_store import decrypt_tokens, encrypt_tokens

# shortcut: static keywords, tune via GMAIL_SYNC_QUERY in .env for your hiring stack
DEFAULT_GMAIL_SYNC_QUERY = (
    "-category:promotions -category:social -label:spam "
    "(in:sent OR subject:(interview OR application OR applied OR recruiter OR hiring OR offer "
    'OR screening OR "job application" OR indeed OR linkedin) '
    "OR from:(indeed.com OR linkedin.com OR greenhouse.io OR lever.co OR workday.com))"
)


def _gmail_list_query(settings: Settings) -> str:
    raw = (settings.gmail_sync_query or "").strip()
    return raw or DEFAULT_GMAIL_SYNC_QUERY


def _sync_interval_sec(settings: Settings) -> float:
    if settings.gmail_sync_interval_sec > 0:
        return settings.gmail_sync_interval_sec
    if settings.mindos_sync_interval_days > 0:
        return settings.mindos_sync_interval_days * 86400.0
    return 0.0


def _b64url(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_part(part: dict) -> tuple[str | None, str | None]:
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if not data:
        return None, None
    raw = _b64url(data)
    if mime == "text/plain":
        return raw, None
    if mime == "text/html":
        return None, raw
    return None, None


def gmail_message_text(payload: dict) -> str:
    if not payload:
        return ""
    plain: list[str] = []
    html: list[str] = []

    def walk(node: dict) -> None:
        p, h = _decode_part(node)
        if p:
            plain.append(p)
        if h:
            html.append(h)
        for part in node.get("parts") or []:
            walk(part)

    walk(payload)
    if plain:
        return "\n\n".join(plain)
    if html:
        return "\n\n".join(_strip_html(h) for h in html)
    data = payload.get("body", {}).get("data")
    if data:
        return _b64url(data)
    return ""


async def ensure_google_access_token(
    session: AsyncSession,
    account: IntegrationAccount,
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> str:
    tokens = decrypt_tokens(settings.mindos_secret_key, account.tokens_enc)
    if force_refresh or access_token_expired(tokens):
        tokens = await refresh_access_token(settings, tokens)
        account.tokens_enc = encrypt_tokens(settings.mindos_secret_key, tokens)
        await session.flush()
    token = tokens.get("access_token")
    if not token:
        raise ValueError("integration missing access_token")
    return token


async def _gmail_sync_pending(session: AsyncSession, user_id) -> bool:
    row = await session.execute(
        select(OutboxEvent.id)
        .where(
            OutboxEvent.event_type == "integrations.gmail.sync",
            OutboxEvent.status.in_(("pending", "processing")),
            OutboxEvent.payload["user_id"].as_string() == str(user_id),
        )
        .limit(1)
    )
    return row.scalar_one_or_none() is not None


async def enqueue_gmail_sync(
    session: AsyncSession,
    *,
    user_id,
    settings: Settings,
    available_at: datetime | None = None,
) -> bool:
    if await _gmail_sync_pending(session, user_id):
        return False
    at = available_at or datetime.now(timezone.utc)
    interval = _sync_interval_sec(settings)
    if interval <= 0 and available_at is None:
        return False
    if available_at is None:
        at = datetime.now(timezone.utc) + timedelta(seconds=interval)
    await enqueue(
        session,
        "integrations.gmail.sync",
        {"user_id": str(user_id)},
        available_at=at,
    )
    return True


async def schedule_gmail_sync(session: AsyncSession, *, user_id, settings: Settings) -> None:
    await enqueue_gmail_sync(session, user_id=user_id, settings=settings)


async def sync_gmail_inbox(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    max_messages: int | None = None,
) -> dict:
    limit = max_messages if max_messages is not None else settings.gmail_sync_max_messages
    query = _gmail_list_query(settings)
    result = await session.execute(
        select(IntegrationAccount).where(
            IntegrationAccount.user_id == user.id,
            IntegrationAccount.provider == "google",
            IntegrationAccount.status == "active",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise ValueError("Google not connected; GET /v1/integrations/google/start")

    token = await ensure_google_access_token(session, account, settings)
    headers = {"Authorization": f"Bearer {token}"}
    ingested = 0
    skipped = 0
    scanned = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        nonlocal_token = {"value": token}

        async def gmail_get(url: str, *, params: dict | None = None) -> httpx.Response:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code == 401:
                nonlocal_token["value"] = await ensure_google_access_token(
                    session, account, settings, force_refresh=True
                )
                headers["Authorization"] = f"Bearer {nonlocal_token['value']}"
                resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp

        msg_ids: list[str] = []
        page_token: str | None = None
        while len(msg_ids) < limit:
            page_size = min(50, limit - len(msg_ids))
            params: dict = {"maxResults": page_size, "q": query}
            if page_token:
                params["pageToken"] = page_token
            listed = await gmail_get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
            )
            page = listed.json()
            batch = [m["id"] for m in page.get("messages", [])]
            msg_ids.extend(batch)
            page_token = page.get("nextPageToken")
            if not batch or not page_token:
                break

        if not msg_ids:
            await session.flush()
            return {"ingested": 0, "skipped": 0, "scanned": 0, "query": query}

        known = await session.execute(
            select(Artifact.external_id).where(
                Artifact.user_id == user.id,
                Artifact.source == "gmail",
                Artifact.external_id.in_(msg_ids),
            )
        )
        skip = set(known.scalars().all())

        for msg_id in msg_ids:
            scanned += 1
            if msg_id in skip:
                skipped += 1
                continue

            meta = (await gmail_get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                params={"format": "full"},
            )).json()
            headers_map = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            subject = headers_map.get("Subject", "(no subject)")
            sender = headers_map.get("From", "")
            body_text = gmail_message_text(meta.get("payload", {})) or meta.get("snippet", "")
            body = f"From: {sender}\nSubject: {subject}\n\n{body_text}"

            artifact = await ingest_manual_note(
                session,
                user=user,
                title=subject,
                body=body,
                settings=settings,
                source="gmail",
                external_id=msg_id,
                metadata={
                    "from": sender,
                    "gmail_thread_id": meta.get("threadId"),
                    "gmail_label_ids": meta.get("labelIds") or [],
                },
            )
            await enqueue(session, "artifact.process", {"artifact_id": str(artifact.id)})
            ingested += 1

    await session.flush()
    return {"ingested": ingested, "skipped": skipped, "scanned": scanned, "query": query}
