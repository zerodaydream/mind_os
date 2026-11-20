"""Structured JSON TurnPlan planner (replaces text-line TurnPrep router)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.contracts.turn_plan import RetrievalFilters, TurnPlan
from backend.services.agents.core.render.prompt_budget import (
    ROUTER_HISTORY_TURNS,
    ROUTER_LLM_MAX_CHARS,
    ROUTER_MAX_OUTPUT_TOKENS,
    clip_text,
)
from backend.services.agents.core.routing.turn_prep import (
    TurnPrep,
    _WORKSPACE_HINT,
    fast_path_turn_prep,
    format_router_history,
    has_execute_verbs,
)

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)

_PLANNER_SYSTEM = """You are MindOS turn planner. Reply with ONE JSON object only (no markdown).
Schema:
{
  "scope": "in" | "clarify" | "sorry",
  "intent": "converse" | "plan" | "execute",
  "queries": ["search rewrite", "..."],
  "filters": {"sender": null, "date_from": null, "date_to": null, "sources": [], "has_attachment": null},
  "entities": ["names"],
  "domains": ["mail"|"calendar"|"files"|"commitments"|"memory"|"web"],
  "writes_external": false,
  "confidence": 0.0-1.0,
  "clarify_reply": "",
  "task": "normalized user task"
}
Rules:
- scope=sorry for off-topic general knowledge; clarify when ambiguous; else in.
- writes_external=true only for calendar/file create/update/delete/sync/rename.
- Read-only calendar questions are intent=plan, writes_external=false, domains include calendar.
- "create a summary" is NOT writes_external.
- queries: 1-3 short retrieval strings; first is the best rewrite.
- Greetings (hi/hello) with no workspace ask: intent=converse, domains=[], queries=[].
"""


async def prepare_turn_plan(
    gateway,
    message: str,
    history: list[dict] | None,
) -> TurnPlan:
    """Produce a TurnPlan via structured JSON (fast path + LLM + fallback)."""
    msg = (message or "").strip()
    fast = fast_path_turn_prep(msg)
    if fast is not None:
        return _plan_from_prep(fast, msg)

    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return _heuristic_plan(msg, history)

    hist = format_router_history(history, max_turns=ROUTER_HISTORY_TURNS)
    prompt = "\n".join(
        [
            _PLANNER_SYSTEM,
            f"Recent conversation:\n{hist}" if hist else "",
            f"User message:\n{clip_text(msg, ROUTER_LLM_MAX_CHARS)}",
            "JSON:",
        ]
    )
    try:
        res = await gateway.complete(
            prompt,
            task=LlmTask.ROUTER,
            max_output_tokens=max(ROUTER_MAX_OUTPUT_TOKENS, 512),
        )
        plan = _parse_plan_json(res.text or "", message=msg)
        if plan is not None:
            return plan
    except Exception:
        logger.warning("turn_plan_llm_failed", exc_info=True)
    return _heuristic_plan(msg, history)


def turn_prep_from_plan(plan: TurnPlan) -> TurnPrep:
    """Legacy TurnPrep view for prefetch helpers still keyed on TurnPrep."""
    if plan.skip_retrieval:
        return TurnPrep(
            in_scope=plan.scope == "in",
            intent=plan.intent,  # type: ignore[arg-type]
            task=plan.task,
            search_query=(plan.queries[0] if plan.queries else plan.task),
            prefetch_mail=False,
            prefetch_commitments=False,
            prefetch_memories=False,
            scope_block=(
                "sorry"
                if plan.scope == "sorry"
                else ("clarify" if plan.scope == "clarify" else "clarify")
            ),
            clarify_reply=plan.clarify_reply,
        )
    domains = set(plan.domains) or _default_plan_domains(plan)
    domains = {d.lower() for d in domains}
    return TurnPrep(
        in_scope=plan.scope == "in",
        intent=plan.intent,  # type: ignore[arg-type]
        task=plan.task,
        search_query=(plan.queries[0] if plan.queries else plan.task),
        prefetch_mail="mail" in domains,
        prefetch_commitments="commitments" in domains,
        prefetch_memories="memory" in domains or "memories" in domains,
        scope_block=(
            "sorry"
            if plan.scope == "sorry"
            else ("clarify" if plan.scope == "clarify" else "clarify")
        ),
        clarify_reply=plan.clarify_reply,
    )


def _default_plan_domains(plan: TurnPlan) -> set[str]:
    """When the planner omits domains, infer prefetch from intent and task text."""
    task = (plan.task or "").strip()
    if plan.intent == "converse" and not _WORKSPACE_HINT.search(task):
        return set()
    out: set[str] = set()
    if plan.intent in ("plan", "execute") or _WORKSPACE_HINT.search(task):
        out.add("mail")
    if plan.intent == "execute" or "commitment" in task.lower():
        out.add("commitments")
    if plan.intent != "converse":
        out.add("memory")
    return out or {"mail"}


def _plan_from_prep(prep: TurnPrep, message: str) -> TurnPlan:
    from backend.services.agents.core.routing.turn_plan_bridge import turn_plan_from_prep

    return turn_plan_from_prep(prep, message)


def _heuristic_plan(message: str, history: list[dict] | None) -> TurnPlan:
    from backend.services.agents.core.routing.turn_prep import fallback_turn_prep

    return _plan_from_prep(fallback_turn_prep(message, history), message)


def _parse_plan_json(raw: str, *, message: str) -> TurnPlan | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    # Tolerate leading prose: take first {...}
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data: dict[str, Any] = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    scope = str(data.get("scope") or "in").lower()
    if scope not in {"in", "clarify", "sorry"}:
        scope = "in"
    intent = str(data.get("intent") or "plan").lower()
    if intent not in {"converse", "plan", "execute"}:
        intent = "plan"
    queries = data.get("queries") or []
    if isinstance(queries, str):
        queries = [queries]
    queries = [str(q).strip() for q in queries if str(q).strip()][:3]
    task = str(data.get("task") or message).strip() or message
    domains = [str(d).lower() for d in (data.get("domains") or []) if str(d).strip()]
    skip_retrieval = bool(data.get("skip_retrieval"))
    if not skip_retrieval and not queries and not domains:
        fast = fast_path_turn_prep(task)
        if fast is not None and fast.skip_retrieval:
            skip_retrieval = True
    if not queries and not skip_retrieval:
        queries = [task]
    if skip_retrieval:
        domains = []
        queries = []
    filters_raw = data.get("filters") if isinstance(data.get("filters"), dict) else {}
    filters = RetrievalFilters(
        sender=filters_raw.get("sender"),
        date_from=filters_raw.get("date_from"),
        date_to=filters_raw.get("date_to"),
        sources=list(filters_raw.get("sources") or []),
        has_attachment=filters_raw.get("has_attachment"),
        entities=list(filters_raw.get("entities") or []),
    )
    writes = bool(data.get("writes_external"))
    if not writes and (intent == "execute" or has_execute_verbs(task)):
        writes = has_execute_verbs(task) or intent == "execute"
    try:
        confidence = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    return TurnPlan(
        scope=scope,  # type: ignore[arg-type]
        intent=intent,  # type: ignore[arg-type]
        queries=queries,
        filters=filters,
        entities=[str(e) for e in (data.get("entities") or []) if str(e).strip()],
        domains=domains,
        writes_external=writes,
        confidence=confidence,
        clarify_reply=str(data.get("clarify_reply") or ""),
        task=task,
        skip_retrieval=skip_retrieval,
    )
