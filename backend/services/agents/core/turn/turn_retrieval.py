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
    PREFETCH_GMAIL_BODY,
    PREFETCH_GMAIL_ROWS,
    PREFETCH_MAIL_PREVIEW,
    PREFETCH_MEMORIES,
    PREFETCH_SEARCH_LIMIT,
    clip_text,
)
from backend.services.retrieval.core.mail_lookup import (
    attachment_lines_from_metadata,
    lookup_indexed_mail,
)
from backend.services.agents.core.render.mail_display import format_mail_display_when
from backend.services.agents.core.routing.turn_prep import TurnPrep
from backend.services.integrations.core.gmail.live_fetch import fetch_live_gmail_turn_rows
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.services.retrieval.core.search_memories import search_memories_ilike

_GMAIL_PREFETCH = re.compile(r"\b(gmail|mail|inbox|sent)\b", re.I)
_NAMED_DOC = re.compile(r"\.(?:pdf|docx?|pptx?|xlsx?|csv|md)\b", re.I)
_FILENAME_TOKEN = re.compile(
    r"\b([\w]+(?:\s[\w]+){0,6}\.(?:pdf|docx?|pptx?|xlsx?|csv|md))\b",
    re.I,
)
_LOCAL_PATH_HINT = re.compile(
    r"(?:^|[\s'\"])(?:~/?|/Users/|\\|[A-Za-z]:\\|\./|\.\./)"
)


def looks_like_named_attachment(message: str) -> bool:
    """Filename like foo.pdf in chat—not an explicit local path."""
    msg = (message or "").strip()
    if not msg or not _NAMED_DOC.search(msg):
        return False
    return not _LOCAL_PATH_HINT.search(msg)


def _mail_vendor_needle(message: str) -> str | None:
    m = re.search(
        r"\b([A-Za-z][\w]*(?:\s+[A-Za-z][\w]*){0,2})\s+team\b",
        message or "",
        re.I,
    )
    return m.group(1).strip() if m else None


def _attachment_filename_needle(message: str) -> str | None:
    m = _FILENAME_TOKEN.search((message or "").strip())
    if not m:
        return None
    parts = m.group(1).strip().split()
    while len(parts) > 1 and parts[0].islower() and "." not in parts[0]:
        parts.pop(0)
    return " ".join(parts)


def _attachment_named_in_gmail_ctx(gmail_ctx: list[dict], filename: str) -> bool:
    low = filename.lower()
    for row in gmail_ctx:
        for rec in (row.get("metadata") or {}).get("attachments") or []:
            if not isinstance(rec, dict):
                continue
            name = (rec.get("filename") or "").lower()
            if low in name or name in low:
                return True
    return False


_HYBRID_GMAIL_WEAK = 0.35


def _hybrid_gmail_weak(results: list[dict], gmail_ids: list[str]) -> bool:
    if not gmail_ids:
        return True
    ranks = [
        float(h.get("rank") or 0.0)
        for h in results
        if (h.get("source") or "").lower() == "gmail"
    ]
    return not ranks or max(ranks) < _HYBRID_GMAIL_WEAK


def turn_in_scope(
    prep: TurnPrep,
    message: str,
    *,
    history: list[dict] | None = None,
    turn_retrieval: TurnRetrieval | None = None,
) -> bool:
    if prep.in_scope:
        return True
    if turn_retrieval and turn_retrieval.has_context and _mail_followup_topic(message, history):
        return True
    return False


def out_of_scope_reply() -> str:
    return (
        "Sorry — I can't help with general knowledge or off-topic questions here. "
        "MindOS is for your mail, calendar, reminders, files, and indexed notes. "
        "What would you like to look up or do in your workspace?"
    )


_CLARIFY_FALLBACK = (
    "What should I look up in your mail, calendar, files, or notes? "
    "A name or date helps."
)


def scope_block_reply(
    message: str,
    *,
    block: str = "clarify",
    clarify: str = "",
) -> str:
    del message  # clarify text comes from router CLARIFY: when live
    if block == "sorry":
        return out_of_scope_reply()
    if (clarify or "").strip():
        return clarify.strip()
    return _CLARIFY_FALLBACK


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


def wants_commitment_context(
    message: str,
    *,
    prefetch_commitments: bool | None = None,
) -> bool:
    if prefetch_commitments is not None:
        return prefetch_commitments
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
    return attachment_lines_from_metadata(metadata)


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


def wants_mail_context(
    message: str,
    source: str | None,
    *,
    prefetch_mail: bool | None = None,
) -> bool:
    if source == "gmail":
        return True
    if prefetch_mail is not None:
        return prefetch_mail
    return bool(_GMAIL_PREFETCH.search(message or ""))


@dataclass
class TurnRetrieval:
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


def preloaded_mail_is_indexed(turn: TurnRetrieval) -> bool:
    """True when prefetch already surfaced Gmail rows or hybrid [gmail] hits."""
    if turn.gmail_ctx:
        return True
    return any((h.get("source") or "").lower() == "gmail" for h in turn.results)


def preloaded_mail_agent_hint(turn: TurnRetrieval) -> str:
    if not preloaded_mail_is_indexed(turn):
        return ""
    return (
        "Indexed Gmail for this question is in Preloaded context below. "
        "Answer from that text and mindos_mail_lookup or mindos_search—do not "
        "mindos_gmail_sync or mindos_process_outbox unless lookup/search lack relevant hits."
    )


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
            body = (g.get("body") or g.get("preview") or "").strip()
            block = header
            drive_lines = _gmail_attachment_url_lines(g.get("metadata"))
            if body:
                prev = clip_text(body, PREFETCH_GMAIL_BODY)
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


async def prefetch_turn_retrieval(
    session: AsyncSession,
    user,
    settings,
    message: str,
    *,
    source: str | None = None,
    intent: str | None = None,
    history: list[dict] | None = None,
    prep: TurnPrep | None = None,
) -> TurnRetrieval:
    message = message.strip()
    if prep is not None and prep.skip_retrieval:
        return TurnRetrieval(message=message, has_context=False)
    mail_flag = prep.prefetch_mail if prep else None
    commit_flag = prep.prefetch_commitments if prep else None
    mem_flag = prep.prefetch_memories if prep else None
    wants_mail = wants_mail_context(message, source, prefetch_mail=mail_flag)
    if looks_like_named_attachment(message):
        wants_mail = True
    topic = _mail_followup_topic(message, history)
    base_q = (prep.search_query if prep else message).strip()
    search_query = f"{topic} {base_q}".strip() if topic else base_q
    hybrid_hits = await hybrid_search_artifacts(
        session,
        user_id=user.id,
        query=search_query,
        settings=settings,
        limit=PREFETCH_SEARCH_LIMIT,
        source=source,
    )
    results = list(hybrid_hits)
    load_commitments = wants_commitment_context(message, prefetch_commitments=commit_flag)
    commitments = (
        await _open_commitment_summaries(session, user.id) if load_commitments else []
    )
    if mem_flag is not None:
        load_memories = mem_flag
    elif intent == "converse":
        load_memories = False
    else:
        load_memories = True
    if load_memories and len(message) >= 8:
        mem_hits = await search_memories_ilike(
            session, user_id=user.id, query=search_query, limit=PREFETCH_MEMORIES
        )
        memories = (
            [f"[{h['kind']}] {h['content']}" for h in mem_hits if h.get("content")]
            if mem_hits
            else await _approved_memories(session, user.id)
        )
    elif load_memories:
        memories = await _approved_memories(session, user.id)
    else:
        memories = []
    recipient = (
        (_mail_recipient_needle(message) or _pronoun_recipient(message, history))
        if wants_mail
        else None
    )
    gmail_ids = [
        h["artifact_id"] for h in results if (h.get("source") or "").lower() == "gmail"
    ]
    if wants_mail:
        mail_limit = min(10, PREFETCH_GMAIL_ROWS)
        gmail_ctx = await lookup_indexed_mail(
            session, user.id, settings, search_query, limit=mail_limit
        )
        if recipient:
            low = recipient.lower()
            gmail_ctx = [
                g
                for g in gmail_ctx
                if low in (g.get("to_addr") or "").lower()
                or low in (g.get("title") or "").lower()
                or low in (g.get("body") or "").lower()
            ]
        if not gmail_ctx and recipient:
            bare = await _ingested_gmail(
                session, user.id, to_needle=recipient, sent_only=True, limit=40
            )
            if bare:
                gmail_ctx = await lookup_indexed_mail(
                    session,
                    user.id,
                    settings,
                    search_query,
                    limit=mail_limit,
                    artifact_ids=[r["artifact_id"] for r in bare],
                )
        elif not gmail_ctx and gmail_ids:
            gmail_ctx = await lookup_indexed_mail(
                session,
                user.id,
                settings,
                search_query,
                limit=mail_limit,
                artifact_ids=gmail_ids,
            )
        elif not gmail_ctx:
            file_needle = _attachment_filename_needle(message)
            vendor = _mail_vendor_needle(message) or _mail_vendor_needle(search_query)
            needle = (
                file_needle
                or (topic[:120] if topic else None)
                or vendor
                or search_query
                or message
            )
            needle = (needle or "").strip()[:120]
            if needle:
                bare = await _ingested_gmail(
                    session, user.id, to_needle=needle, limit=mail_limit
                )
                if bare:
                    gmail_ctx = await lookup_indexed_mail(
                        session,
                        user.id,
                        settings,
                        search_query,
                        limit=mail_limit,
                        artifact_ids=[r["artifact_id"] for r in bare],
                    )
        filename = _attachment_filename_needle(message)
        needs_live_gmail = _hybrid_gmail_weak(results, gmail_ids) and not gmail_ctx
        if filename and gmail_ctx and not _attachment_named_in_gmail_ctx(gmail_ctx, filename):
            needs_live_gmail = True
        if needs_live_gmail:
            live_query = search_query
            if filename and filename.lower() not in live_query.lower():
                live_query = f"{live_query} {filename}".strip()
            try:
                live_rows = await fetch_live_gmail_turn_rows(
                    session,
                    user=user,
                    settings=settings,
                    query=live_query,
                )
            except Exception:
                live_rows = []
            if live_rows:
                seen = {str(g.get("external_id") or g.get("artifact_id")) for g in gmail_ctx}
                for row in live_rows:
                    key = str(row.get("external_id") or row.get("artifact_id"))
                    if key not in seen:
                        gmail_ctx.append(row)
                        seen.add(key)
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
    return TurnRetrieval(
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
