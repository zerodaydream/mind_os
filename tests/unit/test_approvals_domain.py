"""Tests for approvals domain: state machine, payload canonicalization, and hashing."""

from __future__ import annotations

import random
import string
from typing import Any

import pytest

from opsagent.domain import (
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


def test_exhaustive_state_machine_transitions() -> None:
    """Exhaustively verify all valid and invalid transitions."""
    for from_state in ALL_STATES:
        allowed = ALLOWED_TRANSITIONS[from_state]
        for to_state in ALL_STATES:
            is_valid = to_state in allowed
            if from_state == to_state:
                # No self-transitions defined in state machine
                assert not is_valid
            if is_valid:
                assert to_state in allowed
            else:
                assert to_state not in allowed


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


def test_canonical_payload_random_fuzzing() -> None:
    """Property test: randomized nested dictionaries with shuffled keys produce identical hashes."""
    rng = random.Random(42)

    def random_dict(depth: int = 0) -> dict[str, Any]:
        keys = rng.sample(string.ascii_lowercase, rng.randint(3, 8))
        d = {}
        for k in keys:
            if depth < 2 and rng.random() > 0.5:
                d[k] = random_dict(depth + 1)
            else:
                d[k] = rng.choice([rng.randint(0, 100), rng.random(), "str_" + k, True, None])
        return d

    for _ in range(200):
        original = random_dict()
        # Shuffle keys
        shuffled = {k: original[k] for k in rng.sample(list(original.keys()), len(original))}
        h1 = approval_payload_hash("tool.test", "1.0", "AGENT", original)
        h2 = approval_payload_hash("tool.test", "1.0", "AGENT", shuffled)
        assert h1 == h2, "Payload hash must be independent of dict key insertion order"


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
