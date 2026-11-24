"""Retry with full jitter and a circuit breaker (ARCHITECTURE section 11.1). Shared by adapters.

Both take their clock, sleep, and randomness as arguments, so tests need no real waiting.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_s: float = 0.5
    cap_s: float = 8.0
    deadline_s: float = 30.0  # total time across attempts, including sleeps


class CircuitOpen(Exception):
    """The breaker is open: calls are refused until it half-opens."""


class CircuitBreaker:
    """Opens after `threshold` consecutive failures; lets one probe through after `reset_s`."""

    def __init__(
        self,
        *,
        threshold: int = 5,
        reset_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._reset_s = reset_s
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._clock() - self._opened_at >= self._reset_s else "open"

    def allow(self) -> bool:
        state = self.state
        if state == "closed":
            return True
        if state == "half_open" and not self._probing:
            self._probing = True  # exactly one probe at a time
            return True
        return False

    def success(self) -> None:
        self._failures, self._opened_at, self._probing = 0, None, False

    def failure(self) -> None:
        self._failures += 1
        self._probing = False
        if self._failures >= self._threshold or self._opened_at is not None:
            self._opened_at = self._clock()  # a failed probe re-opens for another full period


def full_jitter(
    attempt: int, policy: RetryPolicy, rand: Callable[[], float] = random.random
) -> float:
    """`rand(0, min(cap, base * 2^attempt))`; `attempt` counts from zero."""
    return float(rand() * min(policy.cap_s, policy.base_s * 2**attempt))


def parse_retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a `Retry-After` header (delta-seconds or an HTTP date)."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return float(max(0.0, (when - (now or datetime.now(UTC))).total_seconds()))


async def retry[T](
    op: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    retryable: Callable[[Exception], bool],
    retry_after: Callable[[Exception], float | None] = lambda _e: None,
    breaker: CircuitBreaker | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rand: Callable[[], float] = random.random,
    clock: Callable[[], float] = time.monotonic,
) -> T:
    """Run `op`, retrying errors `retryable` says are transient. The last error is re-raised.

    A server's `Retry-After` replaces the jittered delay when longer. A breaker counts only the
    errors that were worth retrying: a bad request says nothing about the provider's health.
    """
    started = clock()
    for attempt in range(policy.max_attempts):
        if breaker is not None and not breaker.allow():
            raise CircuitOpen("the provider is failing; calls are paused for a moment")
        try:
            result = await op()
        except Exception as exc:
            if not retryable(exc):
                if breaker is not None:
                    breaker.success()  # it answered; the answer was just "no"
                raise
            if breaker is not None:
                breaker.failure()
            delay = max(full_jitter(attempt, policy, rand), retry_after(exc) or 0.0)
            last = attempt + 1 >= policy.max_attempts
            if last or clock() - started + delay > policy.deadline_s:
                raise
            await sleep(delay)
        else:
            if breaker is not None:
                breaker.success()
            return result
    raise AssertionError("unreachable")  # pragma: no cover
