"""One-shot retrieval bundle shared by converse and deep-agent paths."""

from __future__ import annotations

from collections.abc import Callable
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
from backend.services.agents.core.mail_display import format_mail_display_when
from backend.services.agents.core.partial import emit_partial
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
_PREFETCH_SYNC_WORDS = ("sync", "refresh", "latest", "update my", "pull new")


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
    hybrid_hit_count: int = 0
    preflight_actions: list[str] = field(default_factory=list)


def preflight_prompt_block(actions: list[str]) -> str:
    if not actions:
        return ""
    body = "\n".join(f"- {a}" for a in actions)
    return (
        "Steps already taken for this turn (your opening sentence must summarize these for the user):\n"
        f"{body}"
    )


def should_refresh_mail_index(message: str, ctx: TurnContext, *, source: str | None) -> bool:
    if not wants_mail_context(message, source):
        return False
    lower = message.lower()
    if any(w in lower for w in _PREFETCH_SYNC_WORDS):
        return True
    if ctx.hybrid_hit_count == 0 and not ctx.gmail_ctx and not ctx.results:
        return True
    return False


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
            WHERE a.user_id = :uid AND a.source = 'gmail'
            ORDER BY COALESCE(a.occurred_at, a.created_at) DESC
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
            "to_addr": r.to_addr,
            "direction": r.direction,
            "mail_at": r.mail_at,
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
            if preview:
                prev = clip_text(preview, PREFETCH_MAIL_PREVIEW)
                ctx_lines.append(f"{header}\n  {prev}")
            else:
                ctx_lines.append(header)
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
    hybrid_hits = await hybrid_search_artifacts(
        session,
        user_id=user.id,
        query=message,
        settings=settings,
        limit=PREFETCH_SEARCH_LIMIT,
        source=source,
    )
    results = list(hybrid_hits)
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
        hybrid_hit_count=len(hybrid_hits),
    )


async def _announce_mail_refresh(
    gateway,
    message: str,
    *,
    on_partial: Callable[[str], None] | None,
) -> str:
    from backend.common.utils.enums import LlmMode, LlmTask

    fallback = "I'll sync Gmail and search your indexed mail again for that."
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        emit_partial(on_partial, fallback, phase="think")
        return fallback
    prompt = (
        "You are MindOS assistant. The user's question needs fresher Gmail data in the index.\n"
        f"User question: {message}\n\n"
        "Write exactly one short sentence telling the user you will sync Gmail and check again. "
        "Plain text only; no markdown or quotes."
    )
    try:
        res = await gateway.complete(prompt, task=LlmTask.SYNTH, max_output_tokens=64)
        line = (res.text or "").strip() or fallback
    except Exception:
        line = fallback
    emit_partial(on_partial, line, phase="think")
    return line


def format_gmail_sync_thinking(stats: dict, *, processed: int | None) -> str:
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


async def maybe_refresh_mail_index(
    session: AsyncSession,
    user,
    settings,
    message: str,
    ctx: TurnContext,
    *,
    source: str | None = None,
    intent: str | None = None,
    gateway=None,
    on_partial: Callable[[str], None] | None = None,
) -> TurnContext:
    """Gmail sync + outbox drain once when mail is asked but hybrid search missed."""
    from backend.common.config_loader import get_config
    from backend.services.integrations.core.gmail_sync import sync_gmail_inbox
    from backend.worker.consumer import drain_outbox

    if not should_refresh_mail_index(message, ctx, source=source):
        return ctx
    if not get_config().is_enabled("integrations"):
        return ctx
    actions: list[str] = []
    think_text = await _announce_mail_refresh(
        gateway, message, on_partial=on_partial
    )
    actions.append(think_text)
    try:
        stats = await sync_gmail_inbox(session, user=user, settings=settings)
        await session.commit()
    except Exception:
        actions.append("Gmail sync failed — continuing with the current index.")
        think_text = f"{think_text}\n\n**Gmail sync**\n\n- status: failed"
        emit_partial(on_partial, think_text, phase="think")
        ctx.preflight_actions = actions
        return ctx
    ingested = int(stats.get("ingested") or 0)
    scanned = int(stats.get("scanned") or 0)
    actions.append(f"Gmail sync finished (ingested {ingested}, scanned {scanned}).")
    processed: int | None = None
    if ingested > 0:
        think_text = f"{think_text}\n\n_Indexing new mail for search…_"
        emit_partial(on_partial, think_text, phase="think")
        try:
            processed = await drain_outbox(max_events=80)
        except Exception:
            actions.append("Outbox processing failed — some new mail may not be searchable yet.")
            processed = -1
        else:
            actions.append(f"Indexed {processed} outbox event(s) for search.")
    stats_block = format_gmail_sync_thinking(
        stats, processed=processed if ingested > 0 else None
    )
    think_text = f"{think_text}\n\n{stats_block}"
    emit_partial(on_partial, think_text, phase="think")
    actions.append(stats_block)
    refreshed = await prefetch_turn_context(
        session, user, settings, message, source=source, intent=intent
    )
    refreshed.preflight_actions = actions
    return refreshed
