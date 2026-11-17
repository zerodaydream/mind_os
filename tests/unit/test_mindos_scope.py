from backend.services.agents.core.turn.turn_context import (
    message_in_mindos_scope,
    out_of_scope_reply,
)


def test_indeed_adverb_not_workspace_by_itself() -> None:
    assert message_in_mindos_scope("Yes, indeed") is False


def test_trivia_out_of_scope() -> None:
    assert message_in_mindos_scope("what the capital of france?") is False
    assert message_in_mindos_scope("Who invented the telephone?") is False


def test_greeting_in_scope() -> None:
    assert message_in_mindos_scope("Hi") is True
    assert message_in_mindos_scope("thanks!") is True


def test_mail_in_scope() -> None:
    assert message_in_mindos_scope("show my gmail from Krishna") is True


def test_out_of_scope_reply_is_apology() -> None:
    assert "Sorry" in out_of_scope_reply()
    assert "MindOS" in out_of_scope_reply()


def test_short_confirm_in_scope_when_thread_pending() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting tomorrow", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    assert message_in_mindos_scope("yes", history=hist) is True