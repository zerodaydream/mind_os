import pytest

from backend.services.agents.core.routing.turn_prep import (
    fallback_turn_prep,
    fast_path_turn_prep,
    parse_turn_prep,
    prepare_turn,
)


def test_fast_path_greeting_skips_prefetch() -> None:
    prep = fast_path_turn_prep("Hi")
    assert prep is not None
    assert prep.in_scope is True
    assert prep.skip_retrieval is True


def test_parse_turn_prep_full() -> None:
    raw = (
        "SCOPE: yes\n"
        "BLOCK: clarify\n"
        "INTENT: execute\n"
        "TASK: Schedule standup tomorrow 10am\n"
        "SEARCH: standup calendar tomorrow\n"
        "PREFETCH: commitments, memories"
    )
    prep = parse_turn_prep(raw, message="tomorrow 10am", history=None)
    assert prep is not None
    assert prep.intent == "execute"
    assert prep.prefetch_commitments is True
    assert prep.prefetch_memories is True
    assert prep.prefetch_mail is False


def test_fallback_trivia_out_of_scope() -> None:
    prep = fallback_turn_prep("What is the capital of France?", None)
    assert prep.in_scope is False


def test_fallback_statement_trivia_out_of_scope() -> None:
    prep = fallback_turn_prep("explain quantum physics in detail", None)
    assert prep.in_scope is False


def test_vague_followup_detected() -> None:
    from backend.services.agents.core.routing.turn_prep import is_vague_thread_followup

    hist = [
        {"role": "user", "content": "show gmail from alex"},
        {"role": "assistant", "content": "Here are 3 threads."},
    ]
    assert is_vague_thread_followup("tell me more", hist) is True
    assert is_vague_thread_followup("x" * 120, hist) is False
    assert is_vague_thread_followup("capital of France?", hist) is False


def test_intent_token_not_substring() -> None:
    raw = (
        "SCOPE: yes\n"
        "INTENT: converse about execute plans\n"
        "TASK: chat\n"
        "SEARCH: chat\n"
    )
    prep = parse_turn_prep(raw, message="chat", history=None)
    assert prep is not None
    assert prep.intent == "converse"


def test_converse_omitted_prefetch_stays_false() -> None:
    raw = "SCOPE: yes\nINTENT: converse\nTASK: Hi there\nSEARCH: Hi"
    prep = parse_turn_prep(raw, message="Hi there", history=None)
    assert prep is not None
    assert prep.prefetch_mail is False
    assert prep.prefetch_commitments is False
    assert prep.prefetch_memories is False
    assert prep.skip_retrieval is True


def test_vague_does_not_force_in_scope_on_trivia() -> None:
    hist = [
        {"role": "user", "content": "show gmail from alex"},
        {"role": "assistant", "content": "Here are 3 threads."},
    ]
    raw = (
        "SCOPE: no\nBLOCK: sorry\nINTENT: converse\n"
        "TASK: capital of France?\nSEARCH: capital France"
    )
    prep = parse_turn_prep(raw, message="capital of France?", history=hist)
    assert prep is not None
    assert prep.in_scope is False


def test_fallback_mail_in_scope() -> None:
    prep = fallback_turn_prep("show my gmail from Alex", None)
    assert prep.in_scope is True
    assert prep.prefetch_mail is True


@pytest.mark.asyncio
async def test_prepare_turn_degraded_uses_fallback() -> None:
    prep = await prepare_turn(None, "What is the capital of France?", None)
    assert prep.in_scope is False


def test_thread_yes_in_scope() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting tomorrow", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    prep = fallback_turn_prep("yes", hist)
    assert prep.in_scope is True