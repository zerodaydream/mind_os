"""Converse/plan synth: LLM answers over prefetched TurnContext."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.render.partial import emit_partial
from backend.services.agents.core.render.prompt_budget import (
    CHAT_HISTORY_MSG_CHARS,
    CHAT_HISTORY_TURNS,
    SYNTH_MAX_OUTPUT_TOKENS,
    clip_text,
)
from backend.services.agents.core.render.markdown_tables import (
    answer_has_empty_table_shell,
    gmail_rows_markdown_table,
    normalize_markdown_tables,
)
from backend.services.agents.core.turn.turn_context import (
    TurnContext,
    preflight_prompt_block,
    prefetch_turn_context,
)

_CONVERSE_SYSTEM = (
    "You are MindOS assistant. Be brief and conversational. "
    "Do not mention email, commitments, or indexed data unless the user asks. "
    "You cannot create, change, or delete calendar events—never claim you did. "
    "Say to use /do or mention calendar/meetings so the action agent can run. "
    "Never guess titles, authors, or contents of a URL or PDF—tell the user to ask to "
    "summarize the link so MindOS can fetch it."
)


def _looks_cut_off(text: str) -> bool:
    """Heuristic: streamed synth answer ended mid-sentence."""
    t = text.rstrip()
    if len(t) < 24:
        return False
    if t[-1] in ".!?\n":
        return False
    if t.endswith("```") or t.endswith("|"):
        return False
    tail = t.split()[-1].lower() if t.split() else ""
    if tail in {"to", "the", "a", "an", "and", "or", "for", "with", "in", "on", "of", "is", "are"}:
        return True
    return t[-1].isalnum()


async def _synth_stream(
    gateway,
    prompt: str,
    *,
    on_partial: Callable[[str], None] | None = None,
) -> str:
    """Stream synth completion chunks via gateway."""
    parts: list[str] = []
    async for chunk in gateway.stream(
        prompt,
        task=LlmTask.SYNTH,
        max_output_tokens=SYNTH_MAX_OUTPUT_TOKENS,
        on_partial=on_partial,
    ):
        parts.append(chunk)
    return "".join(parts).strip()


async def _finish_synth_answer(gateway, answer: str) -> str:
    """Optionally continue a truncated synth answer."""
    if not _looks_cut_off(answer):
        return answer
    try:
        res = await gateway.complete(
            "Continue the assistant reply below. Output only the remainder; do not repeat.\n\n"
            f"{answer[-800:]}",
            task=LlmTask.SYNTH,
            max_output_tokens=512,
        )
        extra = (res.text or "").strip()
        if extra:
            return f"{answer.rstrip()} {extra}".strip()
    except Exception:
        pass
    return answer


def _format_history(history: list[dict] | None) -> str:
    """Format recent turns for synth prompts."""
    if not history:
        return ""
    lines: list[str] = []
    for m in history[-CHAT_HISTORY_TURNS:]:
        role = m.get("role") or "user"
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {clip_text(content, CHAT_HISTORY_MSG_CHARS)}")
    return "\n".join(lines)


def _ensure_table_rows(answer: str, ctx: TurnContext) -> str:
    """Backfill empty markdown tables from gmail_ctx."""
    if not answer_has_empty_table_shell(answer) or not ctx.gmail_ctx:
        return answer
    built = gmail_rows_markdown_table(ctx.gmail_ctx)
    if not built:
        return answer
    return f"{answer.rstrip()}\n\n{built}"


def _fallback_answer(ctx: TurnContext) -> str:
    """Retrieval-only answer when LLM is degraded."""
    lines: list[str] = []
    if ctx.commitments:
        lines.append("Open commitments:")
        lines.extend(f"- {s}" for s in ctx.commitments[:6])
    if ctx.gmail_ctx:
        lines.append("Ingested Gmail in MindOS:")
        for row in ctx.gmail_ctx[:10]:
            title = row.get("title") or "(no subject)"
            sender = row.get("from_addr") or "unknown"
            preview = (row.get("preview") or "").strip()
            if preview:
                lines.append(f"- {title} (from {sender})\n  {preview[:280]}")
            else:
                lines.append(f"- {title} (from {sender})")
    if ctx.results:
        lines.append("Matching artifacts:")
        for hit in ctx.results[:6]:
            title = hit.get("title") or hit["artifact_id"]
            src = hit.get("source") or "unknown"
            lines.append(f"- [{src}] {title}")
    if not lines:
        return (
            "No matching context in MindOS. Ingest notes or run Gmail sync, then ask again."
        )
    lines.append("(LLM offline — showing retrieved context only.)")
    return "\n".join(lines)


async def run_chat(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    source: str | None = None,
    history: list[dict] | None = None,
    turn_context: TurnContext | None = None,
    plan_style: bool = False,
    on_partial: Callable[[str], None] | None = None,
) -> dict:
    message = message.strip()
    ctx = turn_context or await prefetch_turn_context(
        session, user, settings, message, source=source
    )

    hist_text = _format_history(history)
    if not ctx.has_context and not hist_text and not plan_style:
        if gateway is None or gateway.mode == LlmMode.DEGRADED:
            return {
                "message": message,
                "answer": (
                    "Nothing indexed yet. POST /v1/ingest/manual or Gmail sync, "
                    "ensure the worker is running, then retry."
                ),
                "sources": [],
                "mode": "no_context",
            }
        prompt = f"{_CONVERSE_SYSTEM}\n\nUser: {message}"
        try:
            answer = await _synth_stream(gateway, prompt, on_partial=on_partial)
            if not answer:
                answer = "Hello — what would you like to look up?"
        except Exception:
            answer = "Hello — what would you like to look up?"
        answer = _ensure_table_rows(normalize_markdown_tables(answer), ctx)
        return {"message": message, "answer": answer, "sources": [], "mode": "converse"}

    system = (
        "You are MindOS assistant. Answer from Context and Recent conversation. "
        "Tone: professional and concise, like a capable executive assistant—never sound like a debug log. "
        "Do not say ingested, indexed, context, or 'no additional data is missing' when you can answer. "
        "Open with a direct answer in one short sentence; add a table or list when it helps. "
        "Only mention missing mail or sync when you truly lack the facts. "
        "Do not repeat Gmail sync counts the user already saw in thinking. "
        "Never reply with tables or lists only. "
        "Put a blank line before each heading, list, or table. "
        "Lists: one `1. item` per line; indent nested items with two leading spaces. "
        "Dates: use the human Sent/Date fields from Context (weekday, day, month, year, local time)—never raw ISO. "
        "Tables: pipe markdown with header, `| --- |` separator, one data row per line; every table needs data rows. "
        "For To/From cells use readable names or emails, not long MIME header blobs. "
        "Complete every sentence; do not trail off."
    )
    if plan_style:
        system += (
            " Multi-part questions: handle each part in order—blank line, short lead-in, blank line, then table or list. "
            "Respect numeric limits (e.g. top 10). Use real Subject/Description cells from Context. "
            "For recipient tables use the To: field from Context, not From:. "
            "Example shape:\n"
            "Here are the matching messages:\n\n"
            "| Subject | Description |\n| --- | --- |\n| Indeed Application: AI Developer | summary from mail |"
        )
    if not ctx.has_context:
        base = system if plan_style else _CONVERSE_SYSTEM
        parts = [base]
        if hist_text:
            parts.append(f"Recent conversation:\n{hist_text}")
        parts.append(f"User: {message}")
    else:
        parts = [system, f"Context:\n{ctx.context_text}"]
        pre = preflight_prompt_block(ctx.preflight_actions)
        if pre:
            parts.append(pre)
        if hist_text:
            parts.append(f"Recent conversation:\n{hist_text}")
        parts.append(f"User: {message}")
    prompt = "\n\n".join(parts)

    mode = "synth"
    answer = ""
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        mode = "retrieval_only"
        answer = _fallback_answer(ctx)
    else:
        try:
            answer = await _synth_stream(gateway, prompt, on_partial=on_partial)
            answer = await _finish_synth_answer(gateway, answer)
            if not answer:
                mode = "retrieval_only"
                answer = _fallback_answer(ctx)
        except Exception:
            mode = "retrieval_only"
            answer = _fallback_answer(ctx)

    return {
        "message": message,
        "answer": _ensure_table_rows(normalize_markdown_tables(answer), ctx),
        "sources": ctx.sources,
        "mode": mode,
        "filter_source": source,
    }
