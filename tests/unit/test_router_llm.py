from backend.services.agents.core.routing.router import classify
from backend.services.agents.core.routing.router_llm import (
    _parse_intent_line,
    _parse_turn_routing,
    needs_llm_refine,
    needs_turn_routing,
    thread_pending_execute,
)


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


def test_thread_pending_execute_after_scheduling() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting on calendar", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    assert thread_pending_execute(hist) is True
    assert needs_turn_routing("India", "converse", hist) is True


def test_needs_turn_routing_short_reply_with_history() -> None:
    hist = [{"role": "user", "content": "schedule a meeting tomorrow"}]
    assert needs_turn_routing("yes", "converse", hist) is True
    assert needs_turn_routing("yes", "converse", None) is False


def test_parse_turn_routing_task_line() -> None:
    raw = "INTENT: execute\nTASK: Schedule 30m with manager tomorrow 3pm (user confirmed yes)."
    got = _parse_turn_routing(raw, "yes")
    assert got == (
        "execute",
        "Schedule 30m with manager tomorrow 3pm (user confirmed yes).",
    )
