from backend.services.agents.core.converse.chat import _looks_cut_off


def test_cut_off_on_dangling_to():
    assert _looks_cut_off("Please sync more Gmail messages or data to") is True


def test_complete_sentence_not_cut_off():
    assert _looks_cut_off("Please sync more Gmail messages.") is False