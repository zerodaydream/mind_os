from backend.services.agents.core.partial import emit_partial


def test_emit_partial_phase_kw() -> None:
    seen: list[tuple[str, str]] = []

    def cb(text: str, *, phase: str = "answer") -> None:
        seen.append((text, phase))

    emit_partial(cb, "hi", phase="think")
    assert seen == [("hi", "think")]


def test_emit_partial_legacy_one_arg() -> None:
    seen: list[str] = []

    def cb(text: str) -> None:
        seen.append(text)

    emit_partial(cb, "legacy", phase="think")
    assert seen == ["legacy"]