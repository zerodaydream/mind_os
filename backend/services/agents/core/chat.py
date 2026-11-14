from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.prompt_budget import (
    CHAT_HISTORY_MSG_CHARS,
    CHAT_HISTORY_TURNS,
    SYNTH_MAX_OUTPUT_TOKENS,
    clip_text,
)
from backend.services.agents.core.turn_context import TurnContext, prefetch_turn_context

_CONVERSE_SYSTEM = (
    "You are MindOS assistant. Be brief and conversational. "
    "Do not mention email, commitments, or indexed data unless the user asks."
)


def _format_history(history: list[dict] | None) -> str:
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


def _fallback_answer(ctx: TurnContext) -> str:
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
) -> dict:
    message = message.strip()
    ctx = turn_context or await prefetch_turn_context(
        session, user, settings, message, source=source
    )

    hist_text = _format_history(history)
    if not ctx.has_context and not hist_text:
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
            res = await gateway.complete(
                prompt, task=LlmTask.SYNTH, max_output_tokens=SYNTH_MAX_OUTPUT_TOKENS
            )
            answer = (res.text or "").strip() or "Hello — what would you like to look up?"
        except Exception:
            answer = "Hello — what would you like to look up?"
        return {"message": message, "answer": answer, "sources": [], "mode": "converse"}

    system = (
        "You are MindOS assistant. Answer only from Context; be precise and factual. "
        "Use Markdown lists or tables when asked. If data is missing, say what to sync—do not invent. "
        "Complete every sentence; do not trail off."
    )
    if plan_style:
        system += (
            " This is a multi-step research turn: use the Context to answer fully. "
            "When the user asks for a table, output a Markdown table (Subject | From | Summary) "
            "using only rows supported by the Context; cap at the requested count (e.g. top 10)."
        )
    if not ctx.has_context:
        parts = [_CONVERSE_SYSTEM]
        if hist_text:
            parts.append(f"Recent conversation:\n{hist_text}")
        parts.append(f"User: {message}")
    else:
        parts = [system, f"Context:\n{ctx.context_text}"]
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
            res = await gateway.complete(
                prompt, task=LlmTask.SYNTH, max_output_tokens=SYNTH_MAX_OUTPUT_TOKENS
            )
            answer = (res.text or "").strip()
            if not answer:
                mode = "retrieval_only"
                answer = _fallback_answer(ctx)
        except Exception:
            mode = "retrieval_only"
            answer = _fallback_answer(ctx)

    return {
        "message": message,
        "answer": answer,
        "sources": ctx.sources,
        "mode": mode,
        "filter_source": source,
    }
