from backend.services.agents.core.render.partial import emit_canned_stream


def test_emit_canned_stream_sets_stream_reveal() -> None:
    seen: list[tuple] = []

    def cb(text: str, phase: str = "answer", **kwargs) -> None:
        seen.append((text, phase, kwargs.get("stream_reveal")))

    emit_canned_stream(cb, "  hello  ")
    assert seen == [("hello", "answer", True)]