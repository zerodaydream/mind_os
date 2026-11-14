from backend.services.agents.core.router import classify
from backend.services.agents.core.router_llm import _parse_intent_line, needs_llm_refine


def test_needs_llm_refine_skips_when_plan() -> None:
    intent, body = classify("find me all gmail")
    assert intent == "plan"
    assert needs_llm_refine(body, intent) is False


def test_needs_llm_refine_ambiguous_converse() -> None:
    intent, body = classify("hello there")
    assert intent == "converse"
    assert needs_llm_refine(body, intent) is False
    vague = "converse " + ("x" * 165) + " list table subject"
    assert classify(vague)[0] == "converse"
    assert needs_llm_refine(vague, "converse") is True


def test_parse_intent_line() -> None:
    assert _parse_intent_line("plan") == "plan"
    assert _parse_intent_line("The answer is: execute") == "execute"
