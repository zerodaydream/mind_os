"""Map TurnPrep-shaped fast paths into TurnPlan for prefetch flags."""

from __future__ import annotations

from backend.services.agents.core.contracts.turn_plan import TurnPlan
from backend.services.agents.core.routing.turn_prep import TurnPrep, has_execute_verbs


def turn_plan_from_prep(prep: TurnPrep, message: str) -> TurnPlan:
    """Map TurnPrep fast-path fields into a TurnPlan."""
    domains: list[str] = []
    if prep.prefetch_mail:
        domains.append("mail")
    if prep.prefetch_commitments:
        domains.append("commitments")
    if prep.prefetch_memories:
        domains.append("memory")
    if has_execute_verbs(prep.task or message):
        if "calendar" not in domains:
            domains.append("calendar")

    writes = prep.intent == "execute" or has_execute_verbs(prep.task or message)
    scope = "in" if prep.in_scope else ("sorry" if prep.scope_block == "sorry" else "clarify")
    queries = [prep.search_query or prep.task or message]
    return TurnPlan(
        scope=scope,  # type: ignore[arg-type]
        intent=prep.intent,
        queries=[q for q in queries if q],
        domains=domains,
        writes_external=writes,
        confidence=0.7 if prep.in_scope else 0.3,
        clarify_reply=prep.clarify_reply,
        task=prep.task or message,
        skip_retrieval=prep.skip_retrieval,
    )