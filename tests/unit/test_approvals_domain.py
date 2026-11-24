"""Tests for approvals domain: state machine, payload canonicalization, and hashing."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from mindos.domain import (
    ALLOWED_TRANSITIONS,
    APPROVAL_TTL,
    TERMINAL_STATES,
    ApprovalRecord,
    ApprovalState,
    Principal,
    RiskClass,
    approval_idempotency_key,
    approval_payload_hash,
    canonical_payload,
)

ALL_STATES: tuple[ApprovalState, ...] = (
    "PENDING",
    "APPROVED",
    "APPROVED_EDITED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "CONSUMED",
    "EXECUTED",
    "EXEC_FAILED",
    "INVALIDATED",
)


def test_state_machine_coverage() -> None:
    """Every state must be in ALLOWED_TRANSITIONS."""
    assert set(ALLOWED_TRANSITIONS.keys()) == set(ALL_STATES)


def test_terminal_states_have_no_transitions() -> None:
    """Terminal states must have an empty transition set."""
    for state in TERMINAL_STATES:
        assert ALLOWED_TRANSITIONS[state] == frozenset(), f"{state} should have no transitions"


def test_the_state_machine_only_moves_forward() -> None:
    """No self-loops, PENDING is never re-entered, and every path ends in a terminal state."""
    for state, nexts in ALLOWED_TRANSITIONS.items():
        assert state not in nexts, f"{state} loops on itself"
        assert "PENDING" not in nexts, f"{state} can return to PENDING"

    def reaches_terminal(state: ApprovalState, seen: frozenset[str] = frozenset()) -> bool:
        if state in TERMINAL_STATES:
            return True
        assert state not in seen, f"cycle through {state}"
        return all(reaches_terminal(n, seen | {state}) for n in ALLOWED_TRANSITIONS[state])

    assert all(reaches_terminal(state) for state in ALL_STATES)


def test_only_an_unclaimed_approval_can_be_consumed_and_only_a_consumed_one_executes() -> None:
    sources = {s for s, nexts in ALLOWED_TRANSITIONS.items() if "CONSUMED" in nexts}
    assert sources == {"APPROVED", "APPROVED_EDITED"}
    assert {s for s, nexts in ALLOWED_TRANSITIONS.items() if "EXECUTED" in nexts} == {"CONSUMED"}
    assert {s for s, nexts in ALLOWED_TRANSITIONS.items() if "APPROVED_EDITED" in nexts} == {
        "PENDING"
    }


def test_ttl_defined_for_all_risk_classes() -> None:
    for risk in RiskClass:
        assert risk in APPROVAL_TTL
        assert APPROVAL_TTL[risk].total_seconds() > 0


def test_canonical_payload_is_deterministic_and_compact() -> None:
    d1 = {"b": 2, "a": 1}
    d2 = {"a": 1, "b": 2}
    c1 = canonical_payload("tool.x", "1", "AGENT", d1)
    c2 = canonical_payload("tool.x", "1", "AGENT", d2)
    assert c1 == c2
    assert " " not in c1  # compact: no whitespace between separators
    assert c1 == '{"args":{"a":1,"b":2},"principal":"AGENT","tool":"tool.x","tool_version":"1"}'


def test_payload_hash_matches_sha256() -> None:
    h1 = approval_payload_hash("tool.x", "1", "AGENT", {"a": 1, "b": 2})
    h2 = approval_payload_hash("tool.x", "1", "AGENT", {"b": 2, "a": 1})
    assert h1 == h2
    assert len(h1) == 64


def test_payload_hash_perturbation() -> None:
    base = approval_payload_hash("tool.x", "1", "AGENT", {"key": "value"})
    assert approval_payload_hash("tool.y", "1", "AGENT", {"key": "value"}) != base
    assert approval_payload_hash("tool.x", "2", "AGENT", {"key": "value"}) != base
    assert approval_payload_hash("tool.x", "1", "USER", {"key": "value"}) != base
    assert approval_payload_hash("tool.x", "1", "AGENT", {"key": "value2"}) != base
    assert approval_payload_hash("tool.x", "1", "AGENT", {"key": "value", "extra": 1}) != base


def test_idempotency_key_is_deterministic() -> None:
    k1 = approval_idempotency_key("run_1", "call_1", "hash_abc")
    k2 = approval_idempotency_key("run_1", "call_1", "hash_abc")
    assert k1 == k2
    assert len(k1) == 64
    assert approval_idempotency_key("run_2", "call_1", "hash_abc") != k1
    assert approval_idempotency_key("run_1", "call_2", "hash_abc") != k1
    assert approval_idempotency_key("run_1", "call_1", "hash_xyz") != k1


JSON_LEAF = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
    st.text(),
)
JSON = st.recursive(
    JSON_LEAF,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=6), inner, max_size=4)
    ),
    max_leaves=12,
)
ARGS = st.dictionaries(st.text(max_size=6), JSON, min_size=1, max_size=4)


def _hash(args: Any) -> str:
    return approval_payload_hash("t.x", "1", "AGENT", args)


def _reordered(value: Any, draw: st.DrawFn) -> Any:
    if isinstance(value, dict):
        keys = draw(st.permutations(list(value)))
        return {k: _reordered(value[k], draw) for k in keys}
    if isinstance(value, list):
        return [_reordered(v, draw) for v in value]
    return value


@st.composite
def args_and_reordering(draw: st.DrawFn) -> tuple[dict[str, Any], dict[str, Any]]:
    args = draw(ARGS)
    return args, _reordered(args, draw)


@given(args_and_reordering())
def test_hash_is_independent_of_key_order_at_every_depth(
    pair: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    original, reordered = pair
    assert _hash(original) == _hash(reordered)


@given(ARGS, ARGS)
def test_different_arguments_never_share_a_hash(a: dict[str, Any], b: dict[str, Any]) -> None:
    # compare by canonical JSON so values JSON cannot tell apart (none exist here) don't count
    canon = lambda v: json.dumps(v, sort_keys=True, separators=(",", ":"))  # noqa: E731
    assume(canon(a) != canon(b))
    assert _hash(a) != _hash(b)


@given(ARGS)
def test_the_hash_survives_the_database_round_trip(args: dict[str, Any]) -> None:
    """The engine stores args as JSON and re-hashes what it reads back: they must agree."""
    stored = json.dumps(args, sort_keys=True, separators=(",", ":"))
    assert _hash(json.loads(stored)) == _hash(args)


@given(ARGS, st.sampled_from(["tool", "version", "principal"]))
def test_tool_version_and_principal_are_part_of_the_hash(args: dict[str, Any], field: str) -> None:
    base = ["t.x", "1", "AGENT"]
    other = list(base)
    other[["tool", "version", "principal"].index(field)] += "!"
    assert approval_payload_hash(*base, args) != approval_payload_hash(*other, args)


def test_json_types_are_not_interchangeable_in_the_hash() -> None:
    distinct = [1, "1", True, 1.5, None, [1], {"a": 1}, [], {}, "", 0, False]
    assert len({_hash({"v": v}) for v in distinct}) == len(distinct)


def test_approval_record_immutability() -> None:
    rec = ApprovalRecord(
        id="apr_1",
        run_id="run_1",
        tool="test.tool",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        state="PENDING",
        payload_hash="hash_123",
        idempotency_key="idem_123",
        args_json="{}",
        summary="Test action",
        payload_preview="{}",
        tainted=False,
    )
    with pytest.raises(Exception):
        rec.state = "APPROVED"  # type: ignore[misc]
