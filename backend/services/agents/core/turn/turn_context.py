"""One-shot retrieval bundle shared by converse and deep-agent paths."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import Commitment, Memory
from backend.services.agents.core.render.prompt_budget import (
    PREFETCH_COMMITMENTS,
    PREFETCH_CONTEXT_MAX,
    PREFETCH_CTX_ARTIFACTS,
    PREFETCH_GMAIL_ROWS,
    PREFETCH_MAIL_PREVIEW,
    PREFETCH_MEMORIES,
    PREFETCH_SEARCH_LIMIT,
    clip_text,
)
from backend.services.agents.core.render.mail_display import format_mail_display_when
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.services.retrieval.core.search_memories import search_memories_ilike

_MAIL_KEYWORDS = (
    "email",
    "gmail",
    "reply",
    "replies",
    "inbox",
    "sent",
    "waiting",
    "message",
    "messages",
    "mail",
    "subject",
    "thread",
    "indeed",
    "applied",
    "application",
    "job",
    "jobs",
)
_LIST_PHRASES = ("show all", "list all", "all my", "display all", "every ")


def converse_wants_context(message: str) -> bool:
    """Indexed mail/commitments/search only when the user is actually asking."""
    lower = message.lower().strip()
    if any(w in lower for w in _MAIL_KEYWORDS):
        return True
    triggers = (
        "commit",
        "todo",
        "task",
        "promise",
        "deadline",
        "remember",
        "memory",
        "artifact",
        "indexed",
        "search",
        "find",
        "list",
        "show",
        "help me with",
    )
    if any(t in lower for t in triggers):
        return True
    return len(lower) > 60


_COMMITMENT_WORDS = (
    "commit",
    "todo",
    "task",
    "promise",
    "deadline",
    "follow-up",
    "follow up",
    "followup",
    "owe",
    "owing",
)


def wants_commitment_context(message: str) -> bool:
    """True when message asks about todos or commitments."""
    lower = message.lower()
    return any(w in lower for w in _COMMITMENT_WORDS)


def _mail_followup_topic(message: str, history: list[dict] | None) -> str | None:
    """Prior user turn after at least one assistant reply (conversation follow-up)."""
    msg = message.strip()
    if len(msg) > 100 or not history:
        return None
    after_assistant = False
    for row in reversed(history or []):
        role = row.get("role")
        if role == "assistant":
            after_assistant = True
            continue
        if role != "user":
            continue
        text = (row.get("content") or "").strip()
        if len(text) < 8 or text.lower() == msg.lower():
            continue
        if after_assistant:
            return text[:160]
    return None


def _gmail_attachment_url_lines(metadata: dict | None) -> list[str]:
    """Exact filenames from ingest metadata — agent must not invent names."""
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


def _pronoun_recipient(message: str, history: list[dict] | None) -> str | None:
    if not re.search(r"\b(her|him|them)\b", message, re.I):
        return None
    for row in reversed(history or []):
        text = (row.get("content") or "").strip()
        if needle := _mail_recipient_needle(text) or _mail_to_needle(text):
            return needle
    return None


def _mail_to_needle(message: str) -> str | None:
    """Recipient after ``to`` (e.g. ``… sent to Pat Smith``)."""
    m = re.search(r"\bto\s+([a-z0-9][a-z0-9 .@'-]{1,72})", message.lower())
    if not m:
        return None
    return m.group(1).strip().rstrip("?.!")


def _mail_recipient_needle(message: str) -> str | None:
    """Parse 'sent … to X' recipient filter from natural language."""
    m = re.search(r"\bsent\s+(?:\w+\s+)*to\s+([a-z0-9][a-z0-9 .@'-]{1,72})", message.lower())
    if not m:
        return None
    return m.group(1).strip().rstrip("?.!")


def wants_mail_context(message: str, source: str | None) -> bool:
    """Whether to load Gmail rows for this turn."""
    if source == "gmail":
        return True
    lower = message.lower()
    if any(w in lower for w in _MAIL_KEYWORDS):
        return True
    if any(p in lower for p in _LIST_PHRASES):
        return any(w in lower for w in ("message", "messages", "mail", "email", "inbox", "gmail"))
    return False


@dataclass
class TurnContext:
    """Retrieval and mail context assembled once per turn."""

    message: str
    results: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    gmail_ctx: list[dict] = field(default_factory=list)
    context_text: str = ""
    has_context: bool = False
    hybrid_hit_count: int = 0
    preflight_actions: list[str] = field(default_factory=list)


def preflight_prompt_block(actions: list[str]) -> str:
    """System text listing preflight steps already done."""
    if not actions:
        return ""
    body = "\n".join(f"- {a}" for a in actions)
    return (
        "Steps already taken for this turn (your opening sentence must summarize these for the user):\n"
        f"{body}"
    )


async def _open_commitment_summaries(session: AsyncSession, user_id) -> list[str]:
    """Open commitment summaries from Postgres."""
    rows = await session.execute(
        select(Commitment.summary)
        .where(Commitment.user_id == user_id, Commitment.status == "open")
        .order_by(Commitment.created_at.desc())
        .limit(PREFETCH_COMMITMENTS)
    )
    return [s for (s,) in rows.all() if s]


async def _ingested_gmail(
    session: AsyncSession,
    user_id,
    *,
    limit: int = PREFETCH_GMAIL_ROWS,
    artifact_ids: list[str] | None = None,
    to_needle: str | None = None,
    sent_only: bool = False,
) -> list[dict]:
    """Query ingested Gmail artifacts for context tables."""
    clauses = ["a.user_id = :uid", "a.source = 'gmail'"]
    params: dict = {"uid": user_id, "lim": limit}
    if artifact_ids:
        clauses.append("a.id = ANY(CAST(:ids AS uuid[]))")
        params["ids"] = artifact_ids
    if sent_only:
        clauses.append("a.metadata->>'direction' = 'sent'")
    if to_needle:
        needle = to_needle.strip()
        params["to_pat"] = f"%{needle}%"
        clauses.append(
            "(COALESCE(a.metadata->>'to', '') ILIKE :to_pat "
            "OR COALESCE(a.title, '') ILIKE :to_pat)"
        )
    where = " AND ".join(clauses)
    rows = await session.execute(
        text(
            f"""
            SELECT a.id, a.title, a.metadata,
                   a.metadata->>'from' AS from_addr,
                   a.metadata->>'to' AS to_addr,
                   a.metadata->>'direction' AS direction,
                   a.metadata->>'mail_at' AS mail_at,
                   a.occurred_at,
                   (
                     SELECT LEFT(c.text, 500)
                     FROM artifact_chunks c
                     WHERE c.artifact_id = a.id
                     ORDER BY c.ord
                     LIMIT 1
                   ) AS preview
            FROM artifacts a
            WHERE {where}
            ORDER BY COALESCE(a.occurred_at, a.created_at) DESC
            LIMIT :lim
            """
        ),
        params,
    )
    return [
        {
            "artifact_id": str(r.id),
            "title": r.title,
            "from_addr": r.from_addr,
            "to_addr": r.to_addr,
            "direction": r.direction,
            "mail_at": r.mail_at,
            "preview": r.preview,
            "metadata": r.metadata if isinstance(r.metadata, dict) else {},
        }
        for r in rows
    ]


async def _approved_memories(session: AsyncSession, user_id) -> list[str]:
    """Approved memory strings for the user."""
    rows = await session.execute(
        select(Memory.kind, Memory.content)
        .where(Memory.user_id == user_id, Memory.approved.is_(True))
        .order_by(Memory.updated_at.desc())
        .limit(PREFETCH_MEMORIES)
    )
    return [f"[{k}] {c}" for k, c in rows.all() if c]


def format_context_text(
    *,
    results: list[dict],
    commitments: list[str],
    memories: list[str],
    gmail_ctx: list[dict],
) -> str:
    ctx_lines: list[str] = []
    if memories:
        ctx_lines.append("Approved memories:")
        ctx_lines.extend(f"- {s}" for s in memories)
    if gmail_ctx:
        ctx_lines.append(f"Gmail messages ({len(gmail_ctx)}, newest first):")
        for g in gmail_ctx:
            title = g.get("title") or "(no subject)"
            sender = g.get("from_addr") or "?"
            recip = (g.get("to_addr") or "").strip()
            direction = (g.get("direction") or "?").strip()
            mail_at = format_mail_display_when(g.get("mail_at"))
            header = (
                f"- {title} | {direction} | {mail_at or '?'} | "
                f"To: {recip or '?'} | From: {sender}"
            )
            preview = (g.get("preview") or "").strip().replace("\n", " ")
            block = header
            drive_lines = _gmail_attachment_url_lines(g.get("metadata"))
            if preview:
                prev = clip_text(preview, PREFETCH_MAIL_PREVIEW)
                block = f"{block}\n  {prev}"
            block += "".join(f"\n{ln}" for ln in drive_lines)
            ctx_lines.append(block)
    if commitments:
        ctx_lines.append("Open commitments:")
        ctx_lines.extend(f"- {s}" for s in commitments)
    if results:
        ctx_lines.append("Search hits:")
        for h in results[:PREFETCH_CTX_ARTIFACTS]:
            head = f"- [{h.get('source')}] {h.get('title') or h['artifact_id']}"
            snip = (h.get("snippet") or "").strip().replace("\n", " ")
            if snip:
                ctx_lines.append(f"{head}\n  {clip_text(snip, PREFETCH_MAIL_PREVIEW)}")
            else:
                ctx_lines.append(head)
    return "\n".join(ctx_lines) if ctx_lines else "(no retrieval hits)"


async def prefetch_turn_context(
    session: AsyncSession,
    user,
    settings,
    message: str,
    *,
    source: str | None = None,
    intent: str | None = None,
    history: list[dict] | None = None,
) -> TurnContext:
    message = message.strip()
    if intent == "converse" and not converse_wants_context(message):
        return TurnContext(message=message, has_context=False)
    wants_mail = wants_mail_context(message, source)
    topic = _mail_followup_topic(message, history)
    search_query = f"{topic} {message}".strip() if topic else message
    hybrid_hits = await hybrid_search_artifacts(
        session,
        user_id=user.id,
        query=search_query,
        settings=settings,
        limit=PREFETCH_SEARCH_LIMIT,
        source=source,
    )
    results = list(hybrid_hits)
    commitments = (
        await _open_commitment_summaries(session, user.id)
        if wants_commitment_context(message)
        else []
    )
    if intent in ("plan", "execute") and len(message) >= 8:
        mem_hits = await search_memories_ilike(
            session, user_id=user.id, query=message, limit=PREFETCH_MEMORIES
        )
        memories = (
            [f"[{h['kind']}] {h['content']}" for h in mem_hits if h.get("content")]
            if mem_hits
            else await _approved_memories(session, user.id)
        )
    else:
        memories = await _approved_memories(session, user.id)
    recipient = (
        (_mail_recipient_needle(message) or _pronoun_recipient(message, history))
        if wants_mail
        else None
    )
    gmail_ids = [
        h["artifact_id"] for h in results if (h.get("source") or "").lower() == "gmail"
    ]
    if wants_mail:
        if recipient:
            gmail_ctx = await _ingested_gmail(
                session, user.id, to_needle=recipient, sent_only=True, limit=40
            )
        elif gmail_ids:
            gmail_ctx = await _ingested_gmail(session, user.id, artifact_ids=gmail_ids)
        elif topic:
            gmail_ctx = await _ingested_gmail(
                session, user.id, to_needle=topic[:120], limit=30
            )
        elif results:
            gmail_ctx = []
        else:
            gmail_ctx = await _ingested_gmail(session, user.id)
    else:
        gmail_ctx = []

    if not results and gmail_ctx:
        results = [
            {
                "artifact_id": g["artifact_id"],
                "title": g.get("title"),
                "source": "gmail",
                "rank": 1.0,
            }
            for g in gmail_ctx
        ]

    sources = [
        {
            "artifact_id": h["artifact_id"],
            "title": h.get("title"),
            "source": h.get("source"),
            "rank": h.get("rank"),
        }
        for h in results[:10]
    ]

    context_text = clip_text(
        format_context_text(
            results=results,
            commitments=commitments,
            memories=memories,
            gmail_ctx=gmail_ctx,
        ),
        PREFETCH_CONTEXT_MAX,
    )
    has = bool(results or commitments or gmail_ctx or memories)
    return TurnContext(
        message=message,
        results=results,
        sources=sources,
        commitments=commitments,
        memories=memories,
        gmail_ctx=gmail_ctx,
        context_text=context_text,
        has_context=has,
        hybrid_hit_count=len(hybrid_hits),
    )


def format_gmail_sync_thinking(stats: dict, *, processed: int | None) -> str:
    """Markdown table stats after Gmail sync."""
    ingested = int(stats.get("ingested") or 0)
    skipped = int(stats.get("skipped") or 0)
    scanned = int(stats.get("scanned") or 0)
    lines = [
        "**Gmail sync**",
        "",
        "| step | count |",
        "| --- | ---: |",
        f"| messages scanned | {scanned} |",
        f"| newly ingested | {ingested} |",
        f"| skipped (already indexed) | {skipped} |",
    ]
    if processed is None:
        lines.append("| outbox indexed | — |")
    elif processed < 0:
        lines.append("| outbox indexed | failed |")
    else:
        lines.append(f"| outbox indexed | {processed} |")
    return "\n".join(lines)
