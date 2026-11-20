"""TurnPlan round-trip must preserve greeting fast-path (no prefetch)."""

import pytest

from backend.services.agents.core.routing.turn_plan_bridge import turn_plan_from_prep
from backend.services.agents.core.routing.turn_plan_llm import (
    prepare_turn_plan,
    turn_prep_from_plan,
)
from backend.services.agents.core.routing.turn_prep import fast_path_turn_prep


def test_fast_path_hi_round_trip_skips_retrieval() -> None:
    prep = fast_path_turn_prep("Hi")
    assert prep is not None
    assert prep.skip_retrieval is True
    plan = turn_plan_from_prep(prep, "Hi")
    assert plan.skip_retrieval is True
    assert plan.domains == []
    again = turn_prep_from_plan(plan)
    assert again.skip_retrieval is True
    assert again.prefetch_mail is False


@pytest.mark.asyncio
async def test_heuristic_hi_plan_skips_evidence() -> None:
    plan = await prepare_turn_plan(None, "hello", None)
    assert plan.skip_retrieval is True
    prep = turn_prep_from_plan(plan)
    assert prep.skip_retrieval is True