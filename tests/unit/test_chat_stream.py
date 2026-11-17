from backend.services.agents.core.render.chat_stream import (
    emit_chat_stream,
    emit_chat_stream_reveal,
    format_thinking_for_ui,
    merge_chat_stream_text,
)


def test_emit_chat_stream_phase_kw() -> None:
    seen: list[tuple[str, str]] = []

    def cb(text: str, *, phase: str = "answer") -> None:
        seen.append((text, phase))

    emit_chat_stream(cb, "hi", phase="think")
    assert seen == [("hi", "think")]


def test_merge_chat_stream_text_replaces_cumulative_snapshot() -> None:
    prev = "Locating Sent Emails\n\nI'm checking."
    full = prev + " More detail."
    assert merge_chat_stream_text(prev, full) == full
    assert merge_chat_stream_text(prev, prev) == prev
    assert merge_chat_stream_text("", "Hi") == "Hi"


def test_format_thinking_strips_meta() -> None:
    raw = (
        "I'm currently dissecting the user's request. "
        "I'm cross-referencing this against the system prompt."
    )
    out = format_thinking_for_ui(raw)
    assert "system prompt" not in out.lower()
    assert out == "Working on it…"


def test_emit_chat_stream_keeps_tool_status_think() -> None:
    seen: list[str] = []

    def cb(text: str, *, phase: str = "answer") -> None:
        seen.append(text)

    emit_chat_stream(cb, "[tool] Searching for files…", phase="think")
    assert seen == ["[tool] Searching for files…"]


def test_emit_chat_stream_legacy_one_arg() -> None:
    seen: list[str] = []

    def cb(text: str) -> None:
        seen.append(text)

    emit_chat_stream(cb, "legacy", phase="think")
    assert seen == ["legacy"]


def test_emit_chat_stream_reveal_sets_stream_reveal() -> None:
    seen: list[tuple[str, object]] = []

    def cb(text: str, **kwargs) -> None:
        seen.append((text, kwargs.get("stream_reveal")))

    emit_chat_stream_reveal(cb, "  hello  ")
    assert seen == [("hello", True)]