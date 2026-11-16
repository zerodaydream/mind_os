from backend.services.agents.core.render.partial import emit_partial, merge_stream_text


def test_emit_partial_phase_kw() -> None:
    seen: list[tuple[str, str]] = []

    def cb(text: str, *, phase: str = "answer") -> None:
        seen.append((text, phase))

    emit_partial(cb, "hi", phase="think")
    assert seen == [("hi", "think")]


def test_merge_stream_text_replaces_cumulative_snapshot() -> None:
    prev = "Locating Sent Emails\n\nI'm checking."
    full = prev + " More detail."
    assert merge_stream_text(prev, full) == full
    assert merge_stream_text(prev, prev) == prev
    assert merge_stream_text("", "Hi") == "Hi"


def test_emit_partial_legacy_one_arg() -> None:
    seen: list[str] = []

    def cb(text: str) -> None:
        seen.append(text)

    emit_partial(cb, "legacy", phase="think")
    assert seen == ["legacy"]