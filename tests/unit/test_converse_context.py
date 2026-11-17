from backend.services.agents.core.routing.turn_prep import fast_path_turn_prep
from backend.services.agents.core.turn.turn_retrieval import (
    _mail_recipient_needle,
    wants_commitment_context,
    wants_mail_context,
)


def test_greeting_skips_indexed_context() -> None:
    prep = fast_path_turn_prep("Hi")
    assert prep is not None
    assert prep.skip_retrieval is True


def test_mail_list_skips_commitment_prefetch() -> None:
    q = "give me the list of all gmails i sent to Krishna priya"
    assert wants_commitment_context(q) is False
    assert wants_mail_context(q, None) is True


def test_mail_recipient_needle_parses_sent_to() -> None:
    q = "give me the list of all gmails i sent to Krishna priya"
    assert _mail_recipient_needle(q) == "krishna priya"