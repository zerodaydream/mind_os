"""Indexed Gmail: hybrid pick + full body + attachment lines (prefetch and tools)."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader.settings import Settings
from backend.services.agents.core.render.prompt_budget import clip_text
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
MAIL_LOOKUP_DEFAULT_LIMIT = 3
MAIL_LOOKUP_BODY_MAX = 8_000
MAIL_SEARCH_PREVIEW_MAX = 1_500


async def load_mail_bodies_batch(
    session: AsyncSession,
    artifact_ids: list[str],
    *,
    max_chars: int = MAIL_LOOKUP_BODY_MAX,
) -> dict[str, str]:
    if not artifact_ids:
        return {}
    rows = await session.execute(
        text(
            """
            SELECT c.artifact_id::text,
                   string_agg(c.text, E'\\n' ORDER BY c.ord)
            FROM artifact_chunks c
            WHERE c.artifact_id = ANY(CAST(:ids AS uuid[]))
            GROUP BY c.artifact_id
            """
        ),
        {"ids": artifact_ids},
    )
    out: dict[str, str] = {}
    for aid, raw in rows.all():
        plain = (raw or "").strip().replace("\r\n", "\n")
        if plain:
            out[str(aid)] = clip_text(plain, max_chars)
    return out


async def load_mail_body(
    session: AsyncSession, artifact_id: str | uuid.UUID, *, max_chars: int = MAIL_LOOKUP_BODY_MAX
) -> str:
    bodies = await load_mail_bodies_batch(session, [str(artifact_id)], max_chars=max_chars)
    return bodies.get(str(artifact_id), "")


def attachment_lines_from_metadata(metadata: dict | None) -> list[str]:
    lines: list[str] = []
    for rec in (metadata or {}).get("attachments") or []:
        if not isinstance(rec, dict):
            continue
        name = (rec.get("filename") or "attachment").strip()
        url = (rec.get("drive_url") or "").strip()
        if not url and (fid := (rec.get("drive_file_id") or "").strip()):
            url = f"https://drive.google.com/file/d/{fid}/view"
        if url:
            lines.append(f"  Drive · {name}: {url}")
        else:
            lines.append(f"  attachment · {name}")
    return lines


def format_mail_lookup_text(rows: list[dict]) -> str:
    if not rows:
        return "No matching indexed Gmail messages."
    blocks: list[str] = []
    for row in rows:
        title = row.get("title") or "(no subject)"
        head = (
            f"### {title}\n"
            f"**When:** {row.get('mail_at') or '?'}\n"
            f"**To:** {(row.get('to_addr') or '?').strip()}\n"
            f"**From:** {(row.get('from_addr') or '?').strip()}\n"
            f"**Direction:** {(row.get('direction') or '?').strip()}"
        )
        body = (row.get("body") or row.get("preview") or "").strip()
        if body:
            head = f"{head}\n\n{body}"
        extras = attachment_lines_from_metadata(row.get("metadata"))
        if extras:
            head = f"{head}\n" + "\n".join(extras)
        blocks.append(head)
    return "\n\n".join(blocks)


async def _hydrate_gmail_rows(
    session: AsyncSession, user_id, hits: list[dict]
) -> list[dict]:
    if not hits:
        return []
    ids = [h["artifact_id"] for h in hits]
    db_rows = await session.execute(
        text(
            """
            SELECT a.id, a.title, a.metadata,
                   a.metadata->>'from' AS from_addr,
                   a.metadata->>'to' AS to_addr,
                   a.metadata->>'direction' AS direction,
                   a.metadata->>'mail_at' AS mail_at
            FROM artifacts a
            WHERE a.user_id = :uid AND a.source = 'gmail'
              AND a.id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"uid": user_id, "ids": ids},
    )
    by_id = {
        str(r.id): {
            "artifact_id": str(r.id),
            "title": r.title,
            "from_addr": r.from_addr,
            "to_addr": r.to_addr,
            "direction": r.direction,
            "mail_at": r.mail_at,
            "metadata": r.metadata if isinstance(r.metadata, dict) else {},
            "rank": 0.0,
        }
        for r in db_rows
    }
    id_list = [str(h["artifact_id"]) for h in hits if str(h["artifact_id"]) in by_id]
    bodies = await load_mail_bodies_batch(session, id_list)
    out: list[dict] = []
    for h in hits:
        aid = str(h["artifact_id"])
        row = by_id.get(aid)
        if row is None:
            continue
        row["rank"] = float(h.get("rank") or 0.0)
        body = bodies.get(aid, "")
        row["body"] = body
        row["preview"] = clip_text(body.replace("\n", " "), 600)
        out.append(row)
    return out


async def lookup_indexed_mail(
    session: AsyncSession,
    user_id,
    settings: Settings,
    query: str,
    *,
    limit: int = MAIL_LOOKUP_DEFAULT_LIMIT,
    artifact_ids: list[str] | None = None,
) -> list[dict]:
    if limit <= 0:
        return []
    if artifact_ids:
        hits = [{"artifact_id": aid, "rank": 1.0} for aid in artifact_ids[:limit]]
        return await _hydrate_gmail_rows(session, user_id, hits)
    q = (query or "").strip()
    if not q:
        return []
    hits = await hybrid_search_artifacts(
        session, user_id=user_id, query=q, settings=settings, limit=max(limit * 3, 8), source="gmail"
    )
    if not hits:
        mixed = await hybrid_search_artifacts(
            session, user_id=user_id, query=q, settings=settings, limit=max(limit * 3, 8)
        )
        hits = [h for h in mixed if (h.get("source") or "").lower() == "gmail"]
    seen: set[str] = set()
    ranked: list[dict] = []
    for h in hits:
        aid = str(h["artifact_id"])
        if aid in seen:
            continue
        seen.add(aid)
        ranked.append(h)
        if len(ranked) >= limit:
            break
    return await _hydrate_gmail_rows(session, user_id, ranked)


def gmail_search_preview_text(
    *, body: str, snippet: str, max_chars: int = MAIL_SEARCH_PREVIEW_MAX
) -> str:
    base = (snippet or "").strip().replace("\n", " ")
    if not body:
        return base
    flat = clip_text(body.replace("\n", " "), max_chars)
    return flat if flat else base


async def mail_search_previews_batch(
    session: AsyncSession, artifact_ids: list[str]
) -> dict[str, str]:
    bodies = await load_mail_bodies_batch(
        session, artifact_ids, max_chars=MAIL_SEARCH_PREVIEW_MAX
    )
    return {
        aid: gmail_search_preview_text(body=body, snippet="")
        for aid, body in bodies.items()
    }


async def mail_search_preview(
    session: AsyncSession, artifact_id: str, *, snippet: str, source: str | None
) -> str:
    if (source or "").lower() != "gmail":
        return (snippet or "").strip().replace("\n", " ")
    body = await load_mail_body(session, artifact_id, max_chars=MAIL_SEARCH_PREVIEW_MAX)
    return gmail_search_preview_text(body=body, snippet=snippet)