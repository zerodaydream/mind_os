from backend.services.agents.core.contracts.turn_plan import TurnPlan


def test_resolved_tier_execute_is_two() -> None:
    plan = TurnPlan(intent="execute", writes_external=True, confidence=0.9)
    assert plan.resolved_tier() == 2


def test_resolved_tier_low_confidence_is_zero() -> None:
    plan = TurnPlan(confidence=0.1, scope="in")
    assert plan.resolved_tier() == 0


def test_resolved_tier_grounded_is_one() -> None:
    plan = TurnPlan(intent="plan", confidence=0.8, writes_external=False)
    assert plan.resolved_tier() == 1


def test_insufficient_escalates() -> None:
    plan = TurnPlan(intent="plan", confidence=0.8)
    assert plan.resolved_tier(insufficient=True) == 2
