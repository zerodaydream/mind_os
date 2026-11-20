"""Converse/plan synth: LLM answers over prefetched turn retrieval."""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.render.prompt_budget import SYNTH_MAX_OUTPUT_TOKENS
from backend.services.agents.core.render.prompts import (
    ANSWER_LAYOUT_CORE,
    CONVERSE_EXECUTIVE_TONE,
    CONVERSE_PLAN_STYLE_EXTRA,
    SOURCE_CITATION_RULE,
)
from backend.services.agents.core.render.markdown_tables import (
    answer_has_empty_table_shell,
    gmail_rows_markdown_table,
    normalize_markdown_tables,
    polish_agent_answer,
)
from backend.services.agents.core.routing.turn_prep import (
    TurnPrep,
    fast_path_turn_prep,
    format_router_history,
)
from backend.services.agents.core.turn.turn_retrieval import (
    TurnRetrieval,
    preflight_prompt_block,
    prefetch_turn_retrieval,
)

_CONVERSE_SYSTEM = (
    "You are MindOS assistant. Be brief and conversational. "
    "Refuse general knowledge or off-topic trivia; redirect to mail, calendar, "
    "reminders, files, or indexed notes. "
    "You cannot create, change, or delete calendar events—never claim you did. "
    "Say to use /do or mention calendar/meetings so the action agent can run. "
    "Never guess titles, authors, or contents of a URL or PDF—tell the user to ask to "
    "summarize the link so MindOS can fetch it."
)


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


def _ensure_table_rows(answer: str, ctx: TurnRetrieval) -> str:
    """Backfill empty markdown tables from gmail_ctx."""
    if not answer_has_empty_table_shell(answer) or not ctx.gmail_ctx:
        return answer
    built = gmail_rows_markdown_table(ctx.gmail_ctx)
    if not built:
        return answer
    return f"{answer.rstrip()}\n\n{built}"


_GREETING_FALLBACK = (
    "Hi — I can help with your mail, calendar, files, and commitments. What should we look up?"
)


def _fallback_answer(ctx: TurnRetrieval, *, greeting: bool = False) -> str:
    """Retrieval-only answer when LLM is degraded."""
    if greeting:
        return _GREETING_FALLBACK
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


async def run_converse_turn(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    source: str | None = None,
    history: list[dict] | None = None,
    turn_retrieval: TurnRetrieval | None = None,
    prep: TurnPrep | None = None,
    plan_style: bool = False,
    on_partial: Callable[[str], None] | None = None,
) -> dict:
    message = message.strip()
    intent = prep.intent if prep else "plan"
    fast = fast_path_turn_prep(message)
    sole_greeting = bool(
        (prep and prep.skip_retrieval) or (fast is not None and fast.skip_retrieval)
    )
    ctx = turn_retrieval or await prefetch_turn_retrieval(
        session,
        user,
        settings,
        message,
        source=source,
        intent=intent,
        history=history,
        prep=prep,
    )

    hist_text = format_router_history(history)
    evidence_pack = getattr(ctx, "evidence_pack", None)
    if evidence_pack is None:
        from backend.services.agents.core.contracts.from_turn_retrieval import (
            evidence_pack_from_turn_retrieval,
        )

        evidence_pack = evidence_pack_from_turn_retrieval(ctx)

    if not ctx.has_context and not hist_text and not plan_style:
        if sole_greeting:
            answer = _GREETING_FALLBACK
            if gateway is not None and gateway.mode != LlmMode.DEGRADED:
                prompt = f"{_CONVERSE_SYSTEM}\n\nUser: {message}"
                try:
                    answer = await _synth_stream(gateway, prompt, on_partial=on_partial) or answer
                except Exception:
                    pass
            return {"message": message, "answer": answer, "sources": [], "mode": "converse"}
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
        answer = _ensure_table_rows(
            normalize_markdown_tables(polish_agent_answer(answer)), ctx
        )
        return {"message": message, "answer": answer, "sources": [], "mode": "converse"}

    prompt = _compile_prompt(
        message=message,
        hist_text=hist_text,
        evidence_pack=evidence_pack,
        plan_style=plan_style,
        ctx=ctx,
    )

    mode = "synth"
    answer = ""
    verifier_ok = None
    verifier_violations: list[str] = []
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        mode = "retrieval_only"
        answer = _fallback_answer(ctx, greeting=sole_greeting)
    else:
        try:
            answer = await _synth_stream(gateway, prompt, on_partial=on_partial)
            if not answer:
                mode = "retrieval_only"
                answer = _fallback_answer(ctx, greeting=sole_greeting)
        except Exception:
            mode = "retrieval_only"
            answer = _fallback_answer(ctx, greeting=sole_greeting)

    sources = ctx.sources
    if mode != "retrieval_only" and evidence_pack is not None and answer:
        from backend.services.verification.grounding import verify_grounded_answer

        report = verify_grounded_answer(answer, evidence_pack)
        verifier_ok = report.ok
        verifier_violations = list(report.violations)
        if report.cited_eids:
            sources = [
                {
                    "eid": eid,
                    **(
                        {
                            "artifact_id": evidence_pack.by_eid()[eid].artifact_id,
                            "title": evidence_pack.by_eid()[eid].title,
                        }
                        if eid in evidence_pack.by_eid()
                        else {}
                    ),
                }
                for eid in report.cited_eids
            ]
        elif evidence_pack.items:
            sources = [
                {
                    "eid": e.eid,
                    "artifact_id": e.artifact_id,
                    "title": e.title,
                }
                for e in evidence_pack.items[:8]
            ]
        if not report.ok and report.violations:
            answer = (
                f"{answer.rstrip()}\n\n"
                "_(Some claims could not be verified against retrieved evidence.)_"
            )

    return {
        "message": message,
        "answer": _ensure_table_rows(
            normalize_markdown_tables(polish_agent_answer(answer)), ctx
        ),
        "sources": sources,
        "mode": mode,
        "filter_source": source,
        "verifier_ok": verifier_ok,
        "verifier_violations": verifier_violations,
        "evidence_eids": (
            [e.eid for e in evidence_pack.items] if evidence_pack is not None else []
        ),
    }


def _compile_prompt(
    *,
    message: str,
    hist_text: str,
    evidence_pack,
    plan_style: bool,
    ctx: TurnRetrieval,
) -> str:
    """Build a budgeted prompt via ContextCompiler."""
    from backend.services.context.compiler import ContextCompiler
    from backend.services.context.prompts import load_prompt_module

    core = load_prompt_module("core_system")
    core += f"\n{CONVERSE_EXECUTIVE_TONE}{ANSWER_LAYOUT_CORE}"
    if plan_style:
        core += CONVERSE_PLAN_STYLE_EXTRA
        core += f" {SOURCE_CITATION_RULE}"
    domain = ""
    if ctx.has_context:
        domain = "Domain: mail and workspace retrieval. Prefer Evidence ids over memory."
    pre = preflight_prompt_block(ctx.preflight_actions)
    task = f"User: {message}"
    if pre:
        task = f"{pre}\n\n{task}"
    compiled = ContextCompiler().compile_turn(
        core_system=core,
        domain_modules=[domain] if domain else None,
        evidence=evidence_pack,
        recent_turns=f"Recent conversation:\n{hist_text}" if hist_text else "",
        task=task,
    )
    return compiled.text