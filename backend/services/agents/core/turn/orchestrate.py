"""Route a terminal/API chat turn and persist it."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.agents.core.converse.converse_synth import run_converse_turn
from backend.services.agents.core.routing.router import (
    thread_pending_execute,
    url_from_thread,
)
from dataclasses import replace

from backend.services.agents.core.routing.turn_prep import (
    is_short_confirm,
    is_vague_thread_followup,
)
from backend.services.agents.core.routing.turn_plan_llm import (
    prepare_turn_plan,
    turn_prep_from_plan,
)
from backend.services.agents.core.contracts.evidence import EvidencePack
from backend.services.agents.core.contracts.turn_plan import TurnPlan
from backend.services.agents.core.turn.turn_retrieval import (
    TurnRetrieval,
    prefetch_turn_retrieval,
    scope_block_reply,
    turn_in_scope,
)
from backend.services.agents.core.session.sessions import (
    append_message,
    create_session,
    get_session,
    recent_messages,
)
from backend.services.agents.core.session.title import (
    display_session_title,
    generate_session_title,
    needs_session_title,
)
from backend.services.agents.core.render.chat_stream import emit_chat_stream_reveal
from backend.services.agents.core.render.prompt_budget import CHAT_HISTORY_TURNS
from backend.common.langsmith_setup import begin_turn_trace, flush_traces, tracing_active, unique_run_name
from backend.services.graph.core.deep_agent import run_deep_agent

logger = logging.getLogger(__name__)


def resolve_deep_tier(
    turn_plan: TurnPlan,
    body: str,
    history: list[dict] | None,
    *,
    evidence_pack: EvidencePack | None,
    turn_retrieval: TurnRetrieval,
) -> tuple[bool, int]:
    """Map TurnPlan + retrieval to Tier 1 synth vs Tier 2 deep agent."""
    insufficient = bool(
        evidence_pack.insufficient
        if evidence_pack is not None
        else not turn_retrieval.has_context
    )
    tier = turn_plan.resolved_tier(insufficient=insufficient and turn_plan.writes_external)
    if evidence_pack is not None and evidence_pack.insufficient and not turn_plan.writes_external:
        tier = turn_plan.resolved_tier(insufficient=False)
    use_deep = tier == 2 or (body or "").strip().startswith("/")
    if is_short_confirm(body) and thread_pending_execute(history):
        use_deep = True
        tier = 2
    return use_deep, tier


async def _finish_out_of_scope(
    session,
    chat,
    *,
    user_line: str,
    intent: str,
    prep,
    title_task: asyncio.Task | None,
    on_partial: Callable[[str], None] | None,
) -> dict:
    answer = scope_block_reply(
        user_line, block=prep.scope_block, clarify=prep.clarify_reply
    )
    mode = "out_of_scope" if prep.scope_block == "sorry" else "clarify"
    emit_chat_stream_reveal(on_partial, answer)
    if title_task is not None:
        with contextlib.suppress(Exception):
            await title_task
    await append_message(
        session,
        chat=chat,
        role="assistant",
        content=answer,
        intent=intent,
        metadata={"mode": mode, "sources": []},
    )
    return {
        "message": user_line,
        "answer": answer,
        "sources": [],
        "mode": mode,
        "intent": intent,
        "session_id": str(chat.id),
        "thread_id": str(chat.thread_id),
        "title": display_session_title(chat.title),
    }


async def _synth_plan(
    session,
    user,
    settings,
    gateway,
    body,
    *,
    source,
    history,
    turn_retrieval,
    prep,
    on_partial: Callable[[str], None] | None = None,
) -> tuple[dict, str]:
    """Converse/plan synth fallback when deep agent is unavailable."""
    out = await run_converse_turn(
        session,
        user,
        settings,
        gateway,
        body,
        source=source,
        history=history,
        turn_retrieval=turn_retrieval,
        prep=prep,
        plan_style=True,
        on_partial=on_partial,
    )
    return out, "plan_synth"


async def handle_turn(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    session_id: uuid.UUID | None = None,
    source: str | None = None,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
) -> dict:
    turn_trace = begin_turn_trace() if tracing_active() else None
    ls_cm = None
    ls_run = None
    if tracing_active():
        from langsmith.run_helpers import trace

        ls_cm = trace(
            name=unique_run_name("mindos_handle_turn"),
            run_type="chain",
            inputs={
                "message": message[:4000],
                "session_id": str(session_id) if session_id else None,
                "user_id": str(getattr(user, "id", "")),
                "turn_trace_id": turn_trace,
            },
            metadata={
                "thread_id": str(session_id) if session_id else None,
                "mindos_turn_trace_id": turn_trace,
            },
        )
        ls_run = ls_cm.__enter__()
    try:
        return await _handle_turn_body(
            session,
            user,
            settings,
            gateway,
            message,
            session_id=session_id,
            source=source,
            on_partial=on_partial,
            client_tz=client_tz,
            ls_run=ls_run,
        )
    except Exception as exc:
        if ls_run is not None:
            ls_run.end(error=str(exc))
        raise
    finally:
        exc_info = sys.exc_info()
        if ls_run is not None and exc_info[0] is KeyboardInterrupt:
            try:
                ls_run.end(error="cancelled")
            except Exception:
                pass
        if ls_cm is not None:
            ls_cm.__exit__(*exc_info)
        if exc_info[0] is not None:
            flush_traces()


async def _handle_turn_body(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    session_id: uuid.UUID | None = None,
    source: str | None = None,
    on_partial: Callable[[str], None] | None = None,
    client_tz: str = "",
    ls_run=None,
) -> dict:
    chat = None
    if session_id is not None:
        chat = await get_session(session, session_id=session_id, user_id=user.id)
    if chat is None:
        chat = await create_session(session, user_id=user.id)

    prior_history = await recent_messages(session, session_id=chat.id, limit=CHAT_HISTORY_TURNS)
    user_line = message.strip()

    turn_plan = await prepare_turn_plan(gateway, user_line, prior_history)
    prep = turn_prep_from_plan(turn_plan)
    intent, body = prep.intent, prep.task
    source_url = url_from_thread(body, prior_history)
    if source_url and source_url not in body:
        body = f"{body.strip()}\n\nSource URL: {source_url}"

    title_task: asyncio.Task | None = None
    if needs_session_title(chat.title) and not prior_history:
        title_task = asyncio.create_task(
            generate_session_title(
                gateway, user_line, chat, session, on_partial=on_partial
            )
        )
    await append_message(session, chat=chat, role="user", content=user_line, intent=intent)
    history = await recent_messages(session, session_id=chat.id, limit=CHAT_HISTORY_TURNS)
    history = history[:-1] if history and history[-1].get("role") == "user" else history

    vague_followup = is_vague_thread_followup(user_line, history)
    if prep.blocks_chat_turn() and not vague_followup:
        return await _finish_out_of_scope(
            session,
            chat,
            user_line=user_line,
            intent=intent,
            prep=prep,
            title_task=title_task,
            on_partial=on_partial,
        )

    prep_fetch = prep
    if vague_followup and not prep.in_scope:
        prep_fetch = replace(
            prep,
            prefetch_memories=True,
            prefetch_mail=prep.prefetch_mail or True,
        )

    evidence_pack: EvidencePack | None = None
    if not turn_plan.skip_retrieval:
        try:
            from backend.services.retrieval.core.pipeline import retrieve_evidence_pack

            evidence_pack = await retrieve_evidence_pack(
                session,
                user_id=user.id,
                settings=settings,
                plan=turn_plan,
                message=body,
            )
        except Exception:
            logger.warning("retrieve_evidence_pack_failed", exc_info=True)
            evidence_pack = None

    turn_retrieval = await prefetch_turn_retrieval(
        session,
        user,
        settings,
        body,
        source=source,
        intent=intent,
        history=history,
        prep=prep_fetch,
        evidence_pack=evidence_pack,
    )
    turn_retrieval.evidence_pack = evidence_pack  # type: ignore[attr-defined]

    if prep.blocks_chat_turn() and not turn_in_scope(
        prep, body, history=history, turn_retrieval=turn_retrieval
    ):
        return await _finish_out_of_scope(
            session,
            chat,
            user_line=user_line,
            intent=intent,
            prep=prep,
            title_task=title_task,
            on_partial=on_partial,
        )

    use_deep, tier = resolve_deep_tier(
        turn_plan,
        body,
        history,
        evidence_pack=evidence_pack,
        turn_retrieval=turn_retrieval,
    )

    verifier_ok = None
    verifier_violations: list = []
    evidence_eids: list = []
    if not use_deep:
        out = await run_converse_turn(
            session,
            user,
            settings,
            gateway,
            body,
            source=source,
            history=history,
            turn_retrieval=turn_retrieval,
            prep=prep,
            plan_style=(prep.intent == "plan"),
            on_partial=on_partial,
        )
        mode = "plan_synth" if prep.intent == "plan" else (out.get("mode") or "converse")
        answer = out.get("answer") or ""
        raw_sources = out.get("sources")
        sources = list(raw_sources) if raw_sources is not None else []
        verifier_ok = out.get("verifier_ok")
        verifier_violations = list(out.get("verifier_violations") or [])
        evidence_eids = list(out.get("evidence_eids") or [])
    else:
        try:
            out = await run_deep_agent(
                session,
                user,
                settings,
                gateway,
                body,
                intent=intent,
                thread_id=chat.thread_id,
                turn_retrieval=turn_retrieval,
                history=history,
                on_partial=on_partial,
                client_tz=client_tz,
            )
        except asyncio.CancelledError:
            if title_task is not None:
                title_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await title_task
            return {
                "message": user_line,
                "answer": "",
                "sources": [],
                "mode": "cancelled",
                "intent": intent,
                "session_id": str(chat.id),
                "thread_id": str(chat.thread_id),
                "title": display_session_title(chat.title),
            }
        except OSError as exc:
            out, mode = await _synth_plan(
                session, user, settings, gateway, body,
                source=source, history=history, turn_retrieval=turn_retrieval,
                prep=prep,
                on_partial=on_partial,
            )
            answer = out.get("answer") or ""
            if answer:
                answer = f"{answer}\n\n_(deep agent offline: {exc})_"
            raw_sources = out.get("sources")
            sources = list(raw_sources) if raw_sources is not None else []
        else:
            mode = out.get("mode") or "deep_agent"
            if (
                mode in {"deep_unavailable", "deep_error"}
                and (turn_retrieval.has_context or gateway is not None)
            ):
                err_note = ""
                if mode == "deep_error":
                    err_note = (out.get("answer") or "").strip()
                out, mode = await _synth_plan(
                    session, user, settings, gateway, body,
                    source=source, history=history, turn_retrieval=turn_retrieval,
                    prep=prep,
                    on_partial=on_partial,
                )
                answer = out.get("answer") or ""
                if err_note and answer:
                    answer = f"{answer}\n\n_(deep agent failed; answered from retrieved context)_"
                elif err_note and not answer:
                    answer = err_note
            else:
                answer = out.get("answer") or ""
            raw_sources = out.get("sources")
            sources = list(raw_sources) if raw_sources is not None else []

    if title_task is not None:
        try:
            await title_task
        except Exception:
            pass

    meta: dict = {"mode": mode, "sources": sources}
    if evidence_eids:
        meta["evidence_eids"] = evidence_eids
    if verifier_ok is not None:
        meta["verifier_ok"] = verifier_ok
        meta["verifier_violations"] = verifier_violations
    meta["tier"] = tier

    await append_message(
        session,
        chat=chat,
        role="assistant",
        content=answer,
        intent=intent,
        metadata=meta,
    )

    from backend.services.agents.core.observability.turn_log import record_turn_log
    from backend.services.agents.core.session.session_state import merge_session_state
    from backend.services.agents.core.session.session_state_store import (
        load_chat_session_state,
        save_chat_session_state,
    )

    spend = None
    if gateway is not None and hasattr(gateway, "budget"):
        spend = float(gateway.budget.spent_today_usd or 0.0)
    rerank_margin = evidence_pack.rerank_margin if evidence_pack is not None else None
    timings_ms = evidence_pack.timings_ms if evidence_pack is not None else {}
    try:
        async with session.begin_nested():
            await record_turn_log(
                session,
                session_id=chat.id,
                user_id=getattr(user, "id", None),
                tier=tier,
                mode=mode,
                evidence_eids=evidence_eids,
                verifier_ok=verifier_ok,
                verifier_violations=verifier_violations,
                spend_usd=spend,
                rerank_margin=rerank_margin,
                timings_ms=timings_ms,
            )
    except Exception:
        logger.warning("record_turn_log_failed", exc_info=True)
    try:
        async with session.begin_nested():
            state = await load_chat_session_state(session, chat.id)
            artifact_ids = [
                str(s.get("artifact_id"))
                for s in sources
                if isinstance(s, dict) and s.get("artifact_id")
            ]
            state = merge_session_state(
                state,
                artifact_ids=artifact_ids,
                evidence_eids=evidence_eids,
            )
            await save_chat_session_state(session, chat.id, state)
    except Exception:
        logger.warning("save_chat_session_state_failed", exc_info=True)
    try:
        from backend.services.memories.core.extract_turn import extract_memories_after_turn

        async with session.begin_nested():
            await extract_memories_after_turn(
                session,
                user_id=user.id,
                user_message=user_line,
                gateway=gateway,
            )
    except Exception:
        pass

    result = {
        "message": user_line,
        "answer": answer,
        "sources": sources,
        "mode": mode,
        "intent": intent,
        "session_id": str(chat.id),
        "thread_id": str(chat.thread_id),
        "title": display_session_title(chat.title),
        "tier": tier,
    }
    if ls_run is not None:
        ls_run.end(
            outputs={
                "intent": intent,
                "mode": mode,
                "answer": (answer or "")[:8000],
                "thread_id": str(chat.thread_id),
                "session_id": str(chat.id),
            },
            metadata={"routing": "deep_agent" if mode == "deep_agent" else "synth"},
        )
    flush_traces()
    return result