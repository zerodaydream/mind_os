import base64
import re
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import Artifact, ArtifactChunk, IntegrationAccount, OutboxEvent
from backend.common.database.outbox import enqueue
from backend.services.ingest.core.manual import ingest_manual_note
from backend.services.ingest.core.gmail import (
    attachment_drive_name,
    attachment_drive_view_url,
    attachment_ingest_body_part,
    attachment_skip_reason,
    gmail_format_extras_block,
    gmail_list_attachment_parts,
)
from backend.services.integrations.mcp.drive_store import (
    drive_mcp_download_bytes,
    drive_mcp_upload_bytes,
    gdrive_mcp_configured,
)
from backend.services.integrations.core.google.oauth import (
    access_token_expired,
    refresh_access_token,
)
from backend.services.integrations.core.google.token_store import decrypt_tokens, encrypt_tokens

# shortcut: static keywords, tune via GMAIL_SYNC_QUERY in .env for your hiring stack
DEFAULT_GMAIL_SYNC_QUERY = (
    "-category:promotions -category:social -label:spam "
    "(in:sent OR subject:(interview OR application OR applied OR recruiter OR hiring OR offer "
    'OR screening OR "job application" OR indeed OR linkedin) '
    "OR from:(indeed.com OR linkedin.com OR greenhouse.io OR lever.co OR workday.com))"
)

# Full inbox: promotions tab + marketing with List-Unsubscribe; tune via gmail-ingest --query
DEFAULT_GMAIL_FULL_INGEST_QUERY = "-category:promotions -label:spam -has:unsubscribe"

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_HREF = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""", re.I)


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


_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")


def parse_email_addresses(header: str) -> list[str]:
    if not header:
        return []
    return list(dict.fromkeys(e.lower() for e in _EMAIL.findall(header)))


def gmail_direction(label_ids: list[str] | None) -> str:
    return "sent" if "SENT" in (label_ids or []) else "received"


def gmail_occurred_at(msg: dict) -> datetime | None:
    raw = msg.get("internalDate")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def gmail_header_block(headers_map: dict, *, date_line: str | None = None) -> str:
    subject = headers_map.get("Subject", "(no subject)")
    lines = [f"From: {headers_map.get('From', '')}"]
    to = headers_map.get("To", "")
    if to:
        lines.append(f"To: {to}")
    cc = headers_map.get("Cc", "")
    if cc:
        lines.append(f"Cc: {cc}")
    if date_line:
        lines.append(f"Date: {date_line}")
    elif headers_map.get("Date"):
        lines.append(f"Date: {headers_map['Date']}")
    lines.append(f"Subject: {subject}")
    return "\n".join(lines)


def gmail_artifact_metadata(msg: dict, headers_map: dict) -> tuple[dict, datetime | None]:
    from_hdr = headers_map.get("From", "")
    to = headers_map.get("To", "")
    cc = headers_map.get("Cc", "")
    labels = msg.get("labelIds") or []
    occurred = gmail_occurred_at(msg)
    meta: dict = {
        "from": from_hdr,
        "from_emails": parse_email_addresses(from_hdr),
        "gmail_thread_id": msg.get("threadId"),
        "gmail_label_ids": labels,
        "direction": gmail_direction(labels),
    }
    if to:
        meta["to"] = to
        meta["to_emails"] = parse_email_addresses(to)
    if cc:
        meta["cc"] = cc
    if occurred is not None:
        meta["mail_at"] = occurred.isoformat()
    return meta, occurred


def gmail_artifact_body(headers_map: dict, body_text: str) -> str:
    return f"{gmail_header_block(headers_map)}\n\n{body_text}"


def _merge_chunk_headers(chunk_text: str, header_block: str) -> str:
    if chunk_text.startswith("From:"):
        parts = chunk_text.split("\n\n", 1)
        tail = parts[1] if len(parts) > 1 else ""
        return f"{header_block}\n\n{tail}" if tail else header_block
    return f"{header_block}\n\n{chunk_text}"


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    return _URL.findall(text)


def extract_urls_from_html(html: str) -> list[str]:
    if not html:
        return []
    urls = list(extract_urls_from_text(html))
    for match in _HREF.finditer(html):
        href = match.group(1).strip()
        if href.startswith(("http://", "https://")):
            urls.append(href)
    return urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    return list(dict.fromkeys(u.rstrip(".,;") for u in urls if u))


def gmail_collect_links(payload: dict) -> list[str]:
    found: list[str] = []

    def walk(node: dict) -> None:
        plain, html = _decode_part(node)
        if plain:
            found.extend(extract_urls_from_text(plain))
        if html:
            found.extend(extract_urls_from_html(html))
        for part in node.get("parts") or []:
            walk(part)

    if payload:
        walk(payload)
    return _dedupe_urls(found)


async def gmail_fetch_attachment_bytes(attachment_record: dict) -> bytes:
    """Load raw bytes for a stored attachment (Drive file id in artifact metadata)."""
    file_id = attachment_record.get("drive_file_id")
    if not file_id:
        raise ValueError("attachment missing drive_file_id")
    return await drive_mcp_download_bytes(file_id)


async def gmail_ingest_extras(
    gmail_get,
    msg_id: str,
    payload: dict,
    *,
    show_attachment_progress: bool = False,
) -> tuple[list[str], list[dict], str]:
    links = gmail_collect_links(payload)
    records: list[dict] = []
    body_parts: list[str] = []
    if not gdrive_mcp_configured():
        raise ValueError(
            "gdrive MCP not configured — set GDRIVE_MCP_OAUTH_PATH in .env and "
            "npx @ibarcarty/mcp-server-google-drive auth"
        )
    specs = gmail_list_attachment_parts(payload)
    spec_iter = specs
    if show_attachment_progress and specs:
        from tqdm import tqdm

        spec_iter = tqdm(specs, desc="attachments", unit="file")

    for spec in spec_iter:
        rec: dict = {
            "filename": spec["filename"],
            "mime_type": spec["mime_type"],
            "size": spec["size"],
            "ingested": False,
        }
        reason = attachment_skip_reason(spec["mime_type"], spec["size"], spec["filename"])
        if reason:
            rec["reason"] = reason
            records.append(rec)
            continue
        try:
            resp = await gmail_get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
                f"{msg_id}/attachments/{spec['attachment_id']}"
            )
            data_b64 = resp.json().get("data", "")
            raw = base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4))
            drive_name = attachment_drive_name(msg_id, spec["filename"])
            try:
                file_id = await drive_mcp_upload_bytes(
                    name=drive_name,
                    mime_type=spec["mime_type"],
                    data=raw,
                )
                rec["drive_file_id"] = file_id
                rec["drive_url"] = attachment_drive_view_url(file_id)
            except Exception as exc:
                rec["drive_upload_error"] = type(exc).__name__
            body_part, fail = attachment_ingest_body_part(
                raw, mime=spec["mime_type"], filename=spec["filename"]
            )
            if body_part:
                body_parts.append(body_part)
                rec["ingested"] = True
            elif fail:
                rec["reason"] = fail
            records.append(rec)
        except Exception as exc:
            rec["reason"] = f"download_failed:{type(exc).__name__}"
            records.append(rec)

    block = gmail_format_extras_block(links, records, body_parts)
    return links, records, block


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


def _gmail_needs_backfill(art: Artifact, meta: dict) -> bool:
    return (
        "to" not in meta
        or art.occurred_at is None
        or "direction" not in meta
        or not meta.get("to_emails")
    )


async def _backfill_gmail_artifact(
    session: AsyncSession,
    user_id,
    msg_id: str,
    gmail_get,
) -> bool:
    row = await session.execute(
        select(Artifact).where(
            Artifact.user_id == user_id,
            Artifact.source == "gmail",
            Artifact.external_id == msg_id,
        )
    )
    art = row.scalar_one_or_none()
    if art is None:
        return False
    meta = dict(art.metadata_ or {})
    if not _gmail_needs_backfill(art, meta):
        return False
    msg = (await gmail_get(
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
        params={
            "format": "metadata",
            "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
        },
    )).json()
    headers_map = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    new_meta, occurred = gmail_artifact_metadata(msg, headers_map)
    header_block = gmail_header_block(
        headers_map, date_line=new_meta.get("mail_at") or headers_map.get("Date")
    )
    art.metadata_ = {**meta, **new_meta}
    if occurred is not None:
        art.occurred_at = occurred
    chunk = await session.execute(
        select(ArtifactChunk)
        .where(ArtifactChunk.artifact_id == art.id)
        .order_by(ArtifactChunk.ord)
        .limit(1)
    )
    ch = chunk.scalar_one_or_none()
    if ch is not None:
        ch.text = _merge_chunk_headers(ch.text or "", header_block)
    await enqueue(session, "artifact.process", {"artifact_id": str(art.id)})
    return True


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
    query: str | None = None,
) -> dict:
    if max_messages is None:
        limit = settings.gmail_sync_max_messages
        use_cap = True
    elif max_messages <= 0:
        limit = 0
        use_cap = False
    else:
        limit = max_messages
        use_cap = True
    list_query = (query or "").strip() or _gmail_list_query(settings)
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
    message_bar = None
    if use_cap and limit > 0:
        from tqdm import tqdm

        message_bar = tqdm(total=limit, unit="msg", desc="Gmail", leave=True)

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

        page_token: str | None = None
        saw_messages = False
        while True:
            if use_cap and scanned >= limit:
                break
            page_size = 50
            if use_cap:
                page_size = min(50, limit - scanned)
            params: dict = {"maxResults": page_size, "q": list_query}
            if page_token:
                params["pageToken"] = page_token
            listed = await gmail_get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
            )
            page = listed.json()
            batch = [m["id"] for m in page.get("messages", [])]
            if not batch:
                if not saw_messages:
                    await session.flush()
                    return {"ingested": 0, "skipped": 0, "scanned": 0, "query": list_query}
                break
            saw_messages = True

            known = await session.execute(
                select(Artifact.external_id).where(
                    Artifact.user_id == user.id,
                    Artifact.source == "gmail",
                    Artifact.external_id.in_(batch),
                )
            )
            skip = set(known.scalars().all())

            for msg_id in batch:
                if use_cap and scanned >= limit:
                    break
                scanned += 1
                if msg_id in skip:
                    skipped += 1
                    await _backfill_gmail_artifact(session, user.id, msg_id, gmail_get)
                    if message_bar is not None:
                        message_bar.update(1)
                        message_bar.set_postfix_str(f"ing={ingested} skip={skipped}")
                    continue

                meta = (await gmail_get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    params={"format": "full"},
                )).json()
                headers_map = {
                    h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])
                }
                subject = headers_map.get("Subject", "(no subject)")
                payload = meta.get("payload", {})
                body_text = gmail_message_text(payload) or meta.get("snippet", "")
                links, attachments, extras_block = await gmail_ingest_extras(
                    gmail_get,
                    msg_id,
                    payload,
                    show_attachment_progress=use_cap and 0 < limit <= 5,
                )
                meta_fields, occurred = gmail_artifact_metadata(meta, headers_map)
                if links:
                    meta_fields["links"] = links
                if attachments:
                    meta_fields["attachments"] = attachments
                date_line = meta_fields.get("mail_at") or headers_map.get("Date")
                body = f"{gmail_header_block(headers_map, date_line=date_line)}\n\n{body_text}"
                if extras_block:
                    body = f"{body}\n\n{extras_block}"

                artifact = await ingest_manual_note(
                    session,
                    user=user,
                    title=subject,
                    body=body,
                    settings=settings,
                    source="gmail",
                    external_id=msg_id,
                    metadata=meta_fields,
                    occurred_at=occurred,
                )
                await enqueue(session, "artifact.process", {"artifact_id": str(artifact.id)})
                ingested += 1
                if message_bar is not None:
                    message_bar.update(1)
                    message_bar.set_postfix_str(f"ing={ingested} skip={skipped}")

            if use_cap and scanned >= limit:
                break
            page_token = page.get("nextPageToken")
            if not page_token:
                break
            if not use_cap:
                await session.commit()

    if message_bar is not None:
        message_bar.close()
    await session.flush()
    return {"ingested": ingested, "skipped": skipped, "scanned": scanned, "query": list_query}