"""Retry with full jitter and the circuit breaker (ARCHITECTURE 11.1)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from opsagent.infra.resilience import (
    CircuitBreaker,
    CircuitOpen,
    RetryPolicy,
    full_jitter,
    parse_retry_after,
    retry,
)

POLICY = RetryPolicy(max_attempts=4, base_s=1.0, cap_s=5.0, deadline_s=100)


class Boom(Exception):
    pass


async def no_sleep(_s: float) -> None:
    return None


def test_full_jitter_is_uniform_up_to_a_capped_exponential() -> None:
    assert full_jitter(0, POLICY, lambda: 1.0) == 1.0
    assert full_jitter(2, POLICY, lambda: 1.0) == 4.0
    assert full_jitter(5, POLICY, lambda: 1.0) == 5.0  # capped
    assert full_jitter(3, POLICY, lambda: 0.0) == 0.0


def test_retry_after_accepts_seconds_and_http_dates() -> None:
    assert parse_retry_after("7") == 7.0 and parse_retry_after("-3") == 0.0
    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert parse_retry_after("Wed, 25 Nov 2026 12:00:30 GMT", now) == 30.0
    assert parse_retry_after("garbage") is None and parse_retry_after(None) is None


async def test_a_non_retryable_error_is_raised_at_once() -> None:
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        raise Boom

    with pytest.raises(Boom):
        await retry(op, policy=POLICY, retryable=lambda _e: False, sleep=no_sleep)
    assert calls == 1


async def test_the_deadline_stops_retries_early() -> None:
    now = [0.0]
    calls = 0

    async def op() -> None:
        nonlocal calls
        calls += 1
        now[0] += 10
        raise Boom

    short = RetryPolicy(max_attempts=10, base_s=1, cap_s=1, deadline_s=25)
    with pytest.raises(Boom):
        await retry(
            op, policy=short, retryable=lambda _e: True, sleep=no_sleep, clock=lambda: now[0]
        )
    assert calls == 3  # the third attempt used the budget


def test_the_breaker_opens_half_opens_one_probe_and_recloses() -> None:
    now = [0.0]
    breaker = CircuitBreaker(threshold=2, reset_s=10, clock=lambda: now[0])
    assert breaker.allow()
    breaker.failure()
    breaker.failure()
    assert breaker.state == "open" and not breaker.allow()
    now[0] = 11
    assert breaker.state == "half_open"
    assert breaker.allow() and not breaker.allow()  # exactly one probe
    breaker.failure()  # the probe failed: open for another full period
    assert breaker.state == "open"
    now[0] = 22
    assert breaker.allow()
    breaker.success()
    assert breaker.state == "closed" and breaker.allow()


async def test_an_open_breaker_refuses_the_call() -> None:
    breaker = CircuitBreaker(threshold=1, reset_s=60, clock=lambda: 0.0)
    breaker.failure()

    async def op() -> None:
        raise AssertionError("must not run")

    with pytest.raises(CircuitOpen):
        await retry(op, policy=POLICY, retryable=lambda _e: True, breaker=breaker, sleep=no_sleep)
