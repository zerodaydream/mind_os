from __future__ import annotations

import pytest
from pydantic import ValidationError

from mindos.domain import (
    TERMINAL_EVENTS,
    ReasoningEffort,
    RiskClass,
    bind,
    current,
    events,
    parse_event,
    redact,
    redact_text,
    resolve_effort,
)

E = ReasoningEffort


class TestResolveEffort:
    def test_exact_match(self) -> None:
        r = resolve_effort(E.HIGH, [E.LOW, E.HIGH])
        assert r is not None and r.effort == E.HIGH and not r.adjusted

    def test_falls_back_to_nearest_lower(self) -> None:
        r = resolve_effort(E.XHIGH, [E.MINIMAL, E.LOW, E.HIGH])
        assert r is not None and r.effort == E.HIGH and r.adjusted

    def test_no_lower_uses_lowest_supported(self) -> None:
        r = resolve_effort(E.MINIMAL, [E.MEDIUM, E.HIGH])
        assert r is not None and r.effort == E.MEDIUM and r.adjusted

    def test_no_reasoning_support(self) -> None:
        assert resolve_effort(E.HIGH, []) is None


class TestRisk:
    def test_rank(self) -> None:
        assert RiskClass.R3.rank > RiskClass.R1.rank


class TestCorrelation:
    def test_bind_nests_and_restores(self) -> None:
        assert dict(current()) == {}
        with bind(run_id="r1"):
            with bind(tool_call_id="t1", thread_id=None):
                assert dict(current()) == {"run_id": "r1", "tool_call_id": "t1"}
            assert dict(current()) == {"run_id": "r1"}
        assert dict(current()) == {}

    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown correlation keys"), bind(bogus="x"):
            pass


class TestRedaction:
    @pytest.mark.parametrize(
        "secret",
        [
            "hf_" + "a" * 30,
            "AIza" + "B" * 35,
            "GOCSPX-" + "c" * 20,
            "lsv2_pt_" + "a1" * 10 + "_" + "ff" * 5,
            "Bearer " + "x" * 30,
            "sk-" + "z" * 30,
        ],
    )
    def test_value_shapes(self, secret: str) -> None:
        assert secret not in redact_text(f"auth failed with {secret} today")

    def test_keys_and_nesting(self) -> None:
        out = redact({"api_key": "plain", "nested": [{"password": "p"}, "ok"], "n": 1})
        assert out == {
            "api_key": "[REDACTED]",
            "nested": [{"password": "[REDACTED]"}, "ok"],
            "n": 1,
        }


class TestEvents:
    def test_round_trip_through_json(self) -> None:
        with bind(run_id="r1"):
            original = events.ToolCallFinished(
                run_id="r1", tool_call_id="t1", tool="drive.search", status="ok"
            )
        again = parse_event(original.model_dump_json())
        assert again == original
        assert again.correlation == {"run_id": "r1"}

    def test_unknown_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_event({"type": "nope"})

    def test_events_are_frozen(self) -> None:
        ev = events.Notification(text="hi")
        with pytest.raises(ValidationError):
            ev.text = "changed"  # type: ignore[misc]

    def test_terminal_set(self) -> None:
        assert isinstance(events.RunFinished(), TERMINAL_EVENTS)
        assert not isinstance(events.Notification(text="x"), TERMINAL_EVENTS)
