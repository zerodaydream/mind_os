from backend.services.agents.core.turn.turn_context import (
    _mail_recipient_needle,
    converse_wants_context,
    wants_commitment_context,
)


def test_greeting_skips_indexed_context():
    assert converse_wants_context("Hi") is False
    assert converse_wants_context("Hello!") is False


def test_ambiguous_words_do_not_force_mail_prefetch():
    assert converse_wants_context("Yes, indeed") is False
    assert converse_wants_context("I applied the fix locally") is False
    assert converse_wants_context("this web application is slow") is False
    assert converse_wants_context("good job on the release") is False


def test_commitment_question_loads_context():
    assert converse_wants_context("what are my open commitments") is True


def test_mail_list_skips_commitment_prefetch():
    q = "give me the list of all gmails i sent to Krishna priya"
    assert wants_commitment_context(q) is False


def test_mail_recipient_needle_parses_sent_to():
    q = "give me the list of all gmails i sent to Krishna priya"
    assert _mail_recipient_needle(q) == "krishna priya"