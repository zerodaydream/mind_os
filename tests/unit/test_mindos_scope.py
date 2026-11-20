from backend.services.agents.core.routing.turn_prep import TurnPrep, fallback_turn_prep, parse_turn_prep
from backend.services.agents.core.turn.turn_retrieval import (
    TurnRetrieval,
    out_of_scope_reply,
    preloaded_mail_agent_hint,
    preloaded_mail_is_indexed,
    scope_block_reply,
    turn_in_scope,
)


def test_trivia_out_of_scope() -> None:
    prep = fallback_turn_prep("what the capital of france?", None)
    assert prep.in_scope is False
    assert turn_in_scope(prep, prep.task) is False


def test_greeting_in_scope() -> None:
    from backend.services.agents.core.routing.turn_prep import fast_path_turn_prep

    prep = fast_path_turn_prep("Hi")
    assert prep is not None
    assert turn_in_scope(prep, prep.task) is True


def test_out_of_scope_reply_is_apology() -> None:
    assert "Sorry" in out_of_scope_reply()
    assert "MindOS" in out_of_scope_reply()


def test_router_sorry_block() -> None:
    raw = "SCOPE: no\nBLOCK: sorry\nINTENT: converse\nTASK: trivia"
    prep = parse_turn_prep(raw, message="capital of France?", history=None)
    assert prep is not None
    assert prep.scope_block == "sorry"
    assert "Sorry" in scope_block_reply("x", block=prep.scope_block)


def test_router_clarify_line_used() -> None:
    raw = (
        "SCOPE: no\nBLOCK: clarify\n"
        "CLARIFY: Want me to search Gmail for Propico interview invites?\n"
        "INTENT: plan\nTASK: propico interview"
    )
    prep = parse_turn_prep(raw, message="propico interview links", history=None)
    assert prep is not None
    assert "Propico" in scope_block_reply("x", block=prep.scope_block, clarify=prep.clarify_reply)


def test_vague_defaults_clarify() -> None:
    q = "when was the propico interview and give me the meet links"
    prep = fallback_turn_prep(q, None)
    assert prep.scope_block == "clarify"
    assert "Sorry" not in scope_block_reply(q, block=prep.scope_block)


def test_short_confirm_in_scope_when_thread_pending() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting tomorrow", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    prep = fallback_turn_prep("yes", hist)
    assert turn_in_scope(prep, "yes", history=hist) is True


def test_prep_can_widen_after_retrieval() -> None:
    prep = TurnPrep(
        in_scope=False,
        intent="converse",
        task="tell me more",
        search_query="tell me more",
        prefetch_mail=False,
        prefetch_commitments=False,
        prefetch_memories=False,
    )
    ctx = TurnRetrieval(message="x", has_context=True)
    hist = [
        {"role": "user", "content": "show gmail from alex"},
        {"role": "assistant", "content": "Found 3 messages."},
    ]
    assert turn_in_scope(prep, "tell me more", history=hist, turn_retrieval=ctx) is True


def test_scope_no_without_clarify_continues() -> None:
    prep = TurnPrep(
        in_scope=False,
        intent="plan",
        task="Propico interview date and links",
        search_query="propico interview",
        prefetch_mail=True,
        prefetch_commitments=False,
        prefetch_memories=True,
        scope_block="clarify",
        clarify_reply="",
    )
    assert prep.blocks_chat_turn() is False


def test_clarify_line_blocks_turn() -> None:
    prep = TurnPrep(
        in_scope=False,
        intent="converse",
        task="hmm",
        search_query="hmm",
        prefetch_mail=False,
        prefetch_commitments=False,
        prefetch_memories=False,
        scope_block="clarify",
        clarify_reply="Mail or calendar?",
    )
    assert prep.blocks_chat_turn() is True


def test_preloaded_mail_hint_when_gmail_ctx() -> None:
    ctx = TurnRetrieval(
        message="northflank gpu",
        gmail_ctx=[{"title": "Re: credits", "metadata": {}}],
    )
    assert preloaded_mail_is_indexed(ctx)
    assert "mindos_gmail_sync" in preloaded_mail_agent_hint(ctx)


def test_preloaded_mail_hint_absent_without_mail() -> None:
    ctx = TurnRetrieval(message="hello", results=[{"source": "manual", "artifact_id": "1"}])
    assert not preloaded_mail_is_indexed(ctx)
    assert preloaded_mail_agent_hint(ctx) == ""


def test_trivia_after_assistant_stays_out_of_scope_without_hits() -> None:
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
    assert turn_in_scope(prep, "capital of France?", history=hist) is False


def test_vague_more_with_hits_widens_scope() -> None:
    from backend.services.agents.core.routing.turn_prep import is_vague_thread_followup

    hist = [
        {"role": "user", "content": "show gmail from alex"},
        {"role": "assistant", "content": "Found 3 messages."},
    ]
    assert is_vague_thread_followup("tell me more", hist) is True
    prep = TurnPrep(
        in_scope=False,
        intent="converse",
        task="tell me more",
        search_query="tell me more",
        prefetch_mail=False,
        prefetch_commitments=False,
        prefetch_memories=False,
    )
    ctx = TurnRetrieval(message="x", has_context=True)
    assert turn_in_scope(prep, "tell me more", history=hist, turn_retrieval=ctx) is True