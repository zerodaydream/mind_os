import asyncio
import base64
import hashlib
import re
import sys
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import delete as sql_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.common.database.models import (
    Artifact,
    ArtifactChunk,
    ChunkEmbedding,
    Commitment,
    IntegrationAccount,
    OutboxEvent,
)
from backend.common.database.outbox import enqueue, enqueue_artifact_process
from backend.services.ingest.core.gmail.policy import INGEST_POLICY_VERSION
from backend.services.ingest.core.manual import ingest_manual_note
from backend.services.ingest.core.gmail import (
    attachment_drive_name,
    attachment_drive_view_url,
    attachment_ingest_body_part,
    attachment_skip_reason,
    gmail_format_extras_block,
    gmail_list_attachment_parts,
)
from backend.services.integrations.core.gdrive.drive_store import (
    drive_download_bytes,
    drive_trash_file,
    drive_upload_bytes,
    gdrive_ingest_configured,
)
from backend.services.integrations.core.google.oauth import (
    access_token_expired,
    refresh_access_token,
)
from backend.services.integrations.core.google.token_store import decrypt_tokens, encrypt_tokens

# Full inbox: promotions tab + marketing with List-Unsubscribe; tune via gmail-ingest --query
DEFAULT_GMAIL_FULL_INGEST_QUERY = (
    "-category:promotions -label:spam -has:unsubscribe has:attachment"
)

_URL = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


def _gmail_sync_note(line: str) -> None:
    sys.stderr.write(f"  · Gmail  {line}\n")
    sys.stderr.flush()
_HREF = re.compile(r"""href\s*=\s*['"]([^'"]+)['"]""", re.I)


def _gmail_list_query(settings: Settings) -> str:
    raw = (settings.gmail_sync_query or "").strip()
    return raw or DEFAULT_GMAIL_FULL_INGEST_QUERY


def _gmail_after_clause(latest: datetime | None) -> str:
    if latest is None:
        return ""
    day = latest.astimezone(timezone.utc).date()
    return f"after:{day.year}/{day.month}/{day.day}"


async def _incremental_sync_query(session: AsyncSession, *, user_id, base: str) -> str:
    from sqlalchemy import func

    latest = (
        await session.execute(
            select(func.max(Artifact.occurred_at)).where(
                Artifact.user_id == user_id,
                Artifact.source == "gmail",
            )
        )
    ).scalar()
    tail = _gmail_after_clause(latest)
    return f"{base} {tail}".strip() if tail else base


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
    from backend.services.ingest.core.gmail.text_plain import strip_html_markup

    return strip_html_markup(html)


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
    return await drive_download_bytes(file_id)


async def _ingest_gmail_attachment(
    gmail_get,
    msg_id: str,
    spec: dict,
) -> tuple[dict, str | None]:
    rec: dict = {
        "filename": spec["filename"],
        "mime_type": spec["mime_type"],
        "size": spec["size"],
        "ingested": False,
    }
    reason = attachment_skip_reason(spec["mime_type"], spec["size"], spec["filename"])
    if reason:
        rec["reason"] = reason
        return rec, None
    try:
        resp = await gmail_get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
            f"{msg_id}/attachments/{spec['attachment_id']}"
        )
        data_b64 = resp.json().get("data", "")
        raw = base64.urlsafe_b64decode(data_b64 + "=" * (-len(data_b64) % 4))
        drive_name = attachment_drive_name(msg_id, spec["filename"])
        mime = spec["mime_type"]
        filename = spec["filename"]

        async def _drive_upload() -> tuple[str | None, str | None]:
            try:
                file_id = await drive_upload_bytes(
                    name=drive_name, mime_type=mime, data=raw
                )
                return file_id, None
            except Exception as exc:
                return None, type(exc).__name__

        drive_out, extract_out = await asyncio.gather(
            _drive_upload(),
            asyncio.to_thread(attachment_ingest_body_part, raw, mime=mime, filename=filename),
        )
        file_id, drive_err = drive_out
        body_part, fail = extract_out
        if file_id:
            rec["drive_file_id"] = file_id
            rec["drive_url"] = attachment_drive_view_url(file_id)
        elif drive_err:
            rec["drive_upload_error"] = drive_err
        if body_part:
            rec["ingested"] = True
            return rec, body_part
        if fail:
            rec["reason"] = fail
        return rec, None
    except Exception as exc:
        rec["reason"] = f"download_failed:{type(exc).__name__}"
        return rec, None


async def gmail_ingest_extras(
    gmail_get,
    msg_id: str,
    payload: dict,
    *,
    show_attachment_progress: bool = False,
    subject: str | None = None,
) -> tuple[list[str], list[dict], str]:
    links = gmail_collect_links(payload)
    if not gdrive_ingest_configured():
        raise ValueError(
            "GDrive not configured for attachments — set "
            "GDRIVE_OAUTH_PATH and GDRIVE_TOKEN_PATH in .env"
        )
    specs = gmail_list_attachment_parts(payload)
    sem = asyncio.Semaphore(2)

    async def _one(spec: dict) -> tuple[dict, str | None]:
        async with sem:
            return await _ingest_gmail_attachment(gmail_get, msg_id, spec)

    tasks = [_one(spec) for spec in specs]
    if show_attachment_progress and specs:
        from tqdm.asyncio import tqdm_asyncio

        pairs = await tqdm_asyncio.gather(*tasks, desc="attachments", unit="file")
    else:
        pairs = await asyncio.gather(*tasks)

    records: list[dict] = []
    body_parts: list[str] = []
    label = (subject or msg_id)[:56]
    for rec, body_part in pairs:
        records.append(rec)
        if body_part:
            body_parts.append(body_part)
        err = rec.get("drive_upload_error") or rec.get("reason")
        if err:
            name = rec.get("filename") or "attachment"
            _gmail_sync_note(f"{label} · {name}: {err}")

    if records:
        n_ok = sum(1 for r in records if r.get("ingested"))
        _gmail_sync_note(f"{label} · attachments {n_ok}/{len(records)} ingested")

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


def _gmail_batch_start(batch: list[str], after_msg_id: str | None) -> int:
    if not after_msg_id:
        return 0
    try:
        return batch.index(after_msg_id) + 1
    except ValueError:
        return 0


async def _gmail_artifact_row(
    session: AsyncSession, user_id, msg_id: str
) -> Artifact | None:
    row = await session.execute(
        select(Artifact).where(
            Artifact.user_id == user_id,
            Artifact.source == "gmail",
            Artifact.external_id == msg_id,
        )
    )
    return row.scalar_one_or_none()


def _gmail_chunk_versions_stale(versions: list[str], settings: Settings) -> bool:
    if not versions:
        return True
    want = settings.chunker_version
    return any(v != want for v in versions)


def _gmail_fast_index_stale(
    art: Artifact, settings: Settings, chunk_versions: list[str]
) -> bool:
    meta = art.metadata_ or {}
    if meta.get("ingest_policy_version") != INGEST_POLICY_VERSION:
        return True
    if _gmail_chunk_versions_stale(chunk_versions, settings):
        return True
    atts = meta.get("attachments") or []
    return any(isinstance(a, dict) and not a.get("ingested") for a in atts)


def _gmail_should_verify_body(art: Artifact) -> bool:
    return bool((art.metadata_ or {}).get("attachments"))


async def _fetch_gmail_ingest_payload(
    gmail_get,
    msg_id: str,
    *,
    show_attachment_progress: bool,
) -> dict | None:
    try:
        meta = (
            await gmail_get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                params={"format": "full"},
            )
        ).json()
    except Exception:
        return None
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
        show_attachment_progress=show_attachment_progress,
        subject=subject,
    )
    meta_fields, occurred = gmail_artifact_metadata(meta, headers_map)
    meta_fields["ingest_policy_version"] = INGEST_POLICY_VERSION
    if links:
        meta_fields["links"] = links
    if attachments:
        meta_fields["attachments"] = attachments
    date_line = meta_fields.get("mail_at") or headers_map.get("Date")
    body = f"{gmail_header_block(headers_map, date_line=date_line)}\n\n{body_text}"
    if extras_block:
        body = f"{body}\n\n{extras_block}"
    return {
        "subject": subject,
        "body": body,
        "meta_fields": meta_fields,
        "occurred": occurred,
    }


async def _replace_gmail_artifact(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    msg_id: str,
    payload: dict,
) -> Artifact:
    await _delete_gmail_artifact(session, user.id, msg_id)
    return await ingest_manual_note(
        session,
        user=user,
        title=payload["subject"],
        body=payload["body"],
        settings=settings,
        source="gmail",
        external_id=msg_id,
        metadata=payload["meta_fields"],
        occurred_at=payload["occurred"],
    )


async def _handle_skipped_gmail_message(
    session: AsyncSession,
    *,
    user,
    settings: Settings,
    msg_id: str,
    gmail_get,
    show_attachment_progress: bool,
) -> str:
    """Return ``ingested`` after replace, else ``skipped`` (artifact kept on fetch failure)."""
    art = await _gmail_artifact_row(session, user.id, msg_id)
    if art is None:
        return "skipped"
    rows = await session.execute(
        select(ArtifactChunk.chunker_version).where(ArtifactChunk.artifact_id == art.id)
    )
    versions = list(rows.scalars().all())
    fast_stale = _gmail_fast_index_stale(art, settings, versions)
    if fast_stale or _gmail_should_verify_body(art):
        bundle = await _fetch_gmail_ingest_payload(
            gmail_get, msg_id, show_attachment_progress=show_attachment_progress
        )
        if bundle is None:
            return "skipped"
        new_hash = hashlib.sha256(bundle["body"].encode()).hexdigest()
        if fast_stale or new_hash != art.content_hash:
            await _replace_gmail_artifact(
                session, user=user, settings=settings, msg_id=msg_id, payload=bundle
            )
            return "ingested"
    await _backfill_gmail_artifact(session, user.id, msg_id, gmail_get)
    return "skipped"


async def _delete_gmail_artifact(session: AsyncSession, user_id, msg_id: str) -> bool:
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
    art_id = art.id
    if gdrive_ingest_configured():
        for rec in (art.metadata_ or {}).get("attachments") or []:
            fid = rec.get("drive_file_id")
            if not fid:
                continue
            try:
                await drive_trash_file(str(fid))
            except Exception:
                pass
    chunk_ids = select(ArtifactChunk.id).where(ArtifactChunk.artifact_id == art_id)
    await session.execute(
        update(Commitment)
        .where(Commitment.source_chunk_id.in_(chunk_ids))
        .values(source_chunk_id=None)
    )
    await session.execute(sql_delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))
    await session.execute(sql_delete(Artifact).where(Artifact.id == art_id))
    await session.flush()
    return True


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
    await enqueue_artifact_process(session, str(art.id))
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
    page_token: str | None = None,
    after_msg_id: str | None = None,
    single_list_page: bool = False,
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
    custom_query = bool((query or "").strip() or (settings.gmail_sync_query or "").strip())
    if not custom_query:
        list_query = await _incremental_sync_query(session, user_id=user.id, base=list_query)
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
    last_subject = ""
    if not use_cap:
        _gmail_sync_note("starting ingestion")
    elif use_cap and limit > 0:
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

        list_page_token: str | None = page_token
        saw_messages = False
        capped = False
        next_page_token: str | None = None
        resume_after_msg_id: str | None = None
        list_after_msg_id: str | None = after_msg_id
        total_estimate: int | None = None
        while True:
            if use_cap and scanned >= limit:
                break
            page_size = 50
            if use_cap:
                page_size = min(50, limit - scanned)
            params: dict = {"maxResults": page_size, "q": list_query}
            if list_page_token:
                params["pageToken"] = list_page_token
            if not saw_messages:
                _gmail_sync_note("listing messages…")
            listed = await gmail_get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params=params,
            )
            page = listed.json()
            if total_estimate is None:
                est = page.get("resultSizeEstimate")
                if isinstance(est, int) and est >= 0:
                    total_estimate = est
                    if not use_cap:
                        _gmail_sync_note(f"~{est} messages match query (Gmail estimate)")
            batch = [m["id"] for m in page.get("messages", [])]
            if not batch:
                if not saw_messages:
                    await session.flush()
                    return {
                        "ingested": 0,
                        "skipped": 0,
                        "scanned": 0,
                        "query": list_query,
                        "result_size_estimate": total_estimate,
                        "capped": False,
                        "next_page_token": None,
                        "resume_after_msg_id": None,
                    }
                break
            saw_messages = True
            if not use_cap and message_bar is None:
                from tqdm import tqdm

                bar_total = total_estimate if total_estimate and total_estimate > 0 else None
                message_bar = tqdm(total=bar_total, unit="msg", desc="Gmail", leave=True)
                _gmail_sync_note("processing messages…")
            batch = batch[_gmail_batch_start(batch, list_after_msg_id) :]
            list_after_msg_id = None

            known = await session.execute(
                select(Artifact.external_id).where(
                    Artifact.user_id == user.id,
                    Artifact.source == "gmail",
                    Artifact.external_id.in_(batch),
                )
            )
            skip = set(known.scalars().all())

            show_att = use_cap and 0 < limit <= 20
            last_done_msg_id: str | None = None
            for msg_id in batch:
                if use_cap and scanned >= limit:
                    resume_after_msg_id = last_done_msg_id
                    next_page_token = list_page_token
                    capped = True
                    break
                scanned += 1
                if msg_id in skip:
                    outcome = await _handle_skipped_gmail_message(
                        session,
                        user=user,
                        settings=settings,
                        msg_id=msg_id,
                        gmail_get=gmail_get,
                        show_attachment_progress=show_att,
                    )
                    if outcome == "ingested":
                        ingested += 1
                    else:
                        skipped += 1
                else:
                    if message_bar is not None:
                        message_bar.set_postfix_str("fetching…", refresh=True)
                    bundle = await _fetch_gmail_ingest_payload(
                        gmail_get,
                        msg_id,
                        show_attachment_progress=show_att,
                    )
                    if bundle is None:
                        skipped += 1
                    else:
                        last_subject = (bundle.get("subject") or msg_id)[:44]
                        await ingest_manual_note(
                            session,
                            user=user,
                            title=bundle["subject"],
                            body=bundle["body"],
                            settings=settings,
                            source="gmail",
                            external_id=msg_id,
                            metadata=bundle["meta_fields"],
                            occurred_at=bundle["occurred"],
                        )
                        ingested += 1
                last_done_msg_id = msg_id
                if message_bar is not None:
                    message_bar.update(1)
                    tail = f" · {last_subject}" if last_subject else ""
                    message_bar.set_postfix_str(f"ing={ingested} skip={skipped}{tail}")
            else:
                resume_after_msg_id = None

            if use_cap and scanned >= limit:
                if resume_after_msg_id is None:
                    next_page_token = page.get("nextPageToken")
                    capped = bool(next_page_token)
                break
            list_page_token = page.get("nextPageToken")
            next_page_token = list_page_token
            if not list_page_token:
                break
            if not use_cap:
                await session.commit()
                _gmail_sync_note(f"checkpoint · scanned {scanned} · ingested {ingested}")
            if single_list_page:
                break

    if message_bar is not None:
        message_bar.close()
    await session.flush()
    return {
        "ingested": ingested,
        "skipped": skipped,
        "scanned": scanned,
        "query": list_query,
        "result_size_estimate": total_estimate,
        "capped": capped,
        "next_page_token": next_page_token,
        "resume_after_msg_id": resume_after_msg_id,
    }