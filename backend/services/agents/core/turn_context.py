"""One-shot retrieval bundle shared by converse and deep-agent paths."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import Commitment, Memory
from backend.services.agents.core.prompt_budget import (
    PREFETCH_COMMITMENTS,
    PREFETCH_CONTEXT_MAX,
    PREFETCH_CTX_ARTIFACTS,
    PREFETCH_GMAIL_ROWS,
    PREFETCH_MAIL_PREVIEW,
    PREFETCH_MEMORIES,
    PREFETCH_SEARCH_LIMIT,
    clip_text,
)
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts

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


def wants_mail_context(message: str, source: str | None) -> bool:
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
    message: str
    results: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    commitments: list[str] = field(default_factory=list)
    memories: list[str] = field(default_factory=list)
    gmail_ctx: list[dict] = field(default_factory=list)
    context_text: str = ""
    has_context: bool = False


async def _open_commitment_summaries(session: AsyncSession, user_id) -> list[str]:
    rows = await session.execute(
        select(Commitment.summary)
        .where(Commitment.user_id == user_id, Commitment.status == "open")
        .order_by(Commitment.created_at.desc())
        .limit(PREFETCH_COMMITMENTS)
    )
    return [s for (s,) in rows.all() if s]


async def _ingested_gmail(session: AsyncSession, user_id, *, limit: int = PREFETCH_GMAIL_ROWS) -> list[dict]:
    rows = await session.execute(
        text(
            """
            SELECT a.id, a.title, a.metadata->>'from' AS from_addr,
                   (
                     SELECT LEFT(c.text, 500)
                     FROM artifact_chunks c
                     WHERE c.artifact_id = a.id
                     ORDER BY c.ord
                     LIMIT 1
                   ) AS preview
            FROM artifacts a
            WHERE a.user_id = :uid AND a.source = 'gmail'
            ORDER BY a.created_at DESC
            LIMIT :lim
            """
        ),
        {"uid": user_id, "lim": limit},
    )
    return [
        {
            "artifact_id": str(r.id),
            "title": r.title,
            "from_addr": r.from_addr,
            "preview": r.preview,
        }
        for r in rows
    ]


async def _approved_memories(session: AsyncSession, user_id) -> list[str]:
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
    if commitments:
        ctx_lines.append("Open commitments:")
        ctx_lines.extend(f"- {s}" for s in commitments)
    if gmail_ctx:
        ctx_lines.append(f"Ingested Gmail messages ({len(gmail_ctx)} in MindOS, newest first):")
        for g in gmail_ctx:
            title = g.get("title") or "(no subject)"
            sender = g.get("from_addr") or "?"
            preview = (g.get("preview") or "").strip().replace("\n", " ")
            if preview:
                prev = clip_text(preview, PREFETCH_MAIL_PREVIEW)
                ctx_lines.append(f"- {title} | From: {sender}\n  {prev}")
            else:
                ctx_lines.append(f"- {title} | From: {sender}")
    if results:
        ctx_lines.append("Search hits:")
        ctx_lines.extend(
            f"- [{h.get('source')}] {h.get('title') or h['artifact_id']}"
            for h in results[:PREFETCH_CTX_ARTIFACTS]
        )
    return "\n".join(ctx_lines) if ctx_lines else "(no retrieval hits)"


async def prefetch_turn_context(
    session: AsyncSession,
    user,
    settings,
    message: str,
    *,
    source: str | None = None,
    intent: str | None = None,
) -> TurnContext:
    message = message.strip()
    if intent == "converse" and not converse_wants_context(message):
        return TurnContext(message=message, has_context=False)
    wants_mail = wants_mail_context(message, source)
    results = await hybrid_search_artifacts(
        session,
        user_id=user.id,
        query=message,
        settings=settings,
        limit=PREFETCH_SEARCH_LIMIT,
        source=source,
    )
    commitments = await _open_commitment_summaries(session, user.id)
    memories = await _approved_memories(session, user.id)
    gmail_ctx = await _ingested_gmail(session, user.id) if wants_mail else []

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
    )
