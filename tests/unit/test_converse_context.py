from backend.services.agents.core.turn_context import converse_wants_context


def test_greeting_skips_indexed_context():
    assert converse_wants_context("Hi") is False
    assert converse_wants_context("Hello!") is False


def test_commitment_question_loads_context():
    assert converse_wants_context("what are my open commitments") is True