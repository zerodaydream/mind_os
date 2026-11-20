from backend.services.agents.core.routing.turn_plan_llm import _parse_plan_json


def test_parse_plan_json_create_summary_not_write() -> None:
    raw = """{
      "scope": "in",
      "intent": "plan",
      "queries": ["summary of mail"],
      "filters": {},
      "entities": [],
      "domains": ["mail"],
      "writes_external": false,
      "confidence": 0.85,
      "task": "create a summary of that mail"
    }"""
    plan = _parse_plan_json(raw, message="create a summary of that mail")
    assert plan is not None
    assert plan.writes_external is False
    assert plan.resolved_tier() == 1


def test_parse_plan_json_schedule_is_write() -> None:
    raw = """{
      "scope": "in",
      "intent": "execute",
      "queries": ["standup"],
      "domains": ["calendar"],
      "writes_external": true,
      "confidence": 0.9,
      "task": "schedule standup tomorrow 10am"
    }"""
    plan = _parse_plan_json(raw, message="schedule standup tomorrow 10am")
    assert plan is not None
    assert plan.writes_external is True
    assert plan.resolved_tier() == 2
