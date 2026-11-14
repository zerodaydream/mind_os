"""Sentence-safe prompt clipping."""

from backend.services.agents.core.prompt_budget import clip_text


def test_clip_text_unchanged_when_short():
    assert clip_text("hello", 100) == "hello"


def test_clip_text_prefers_sentence_boundary():
    text = "First sentence is here. Second sentence is much longer and should drop."
    out = clip_text(text, 42)
    assert out.endswith("…")
    assert "Second" not in out
    assert out.rstrip("…").endswith(".")


def test_chat_history_budget_room():
    from backend.services.agents.core.prompt_budget import CHAT_HISTORY_MSG_CHARS, SYNTH_MAX_OUTPUT_TOKENS

    assert CHAT_HISTORY_MSG_CHARS >= 1_600
    assert SYNTH_MAX_OUTPUT_TOKENS >= 2_048


def test_clip_text_word_boundary_fallback():
    text = "abcdefghijklmnop" * 20
    out = clip_text(text, 80)
    assert len(out) <= 81
    assert out.endswith("…")
    assert " " in text or out.rstrip("…") in text
