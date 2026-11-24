"""The Google Calendar adapter against a scripted API: auth, retries, mapping, reconcile."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from mindos.domain import (
    AccessToken,
    AuthStatus,
    EventDraft,
    EventPatch,
    ProviderError,
    ReauthRequired,
    ScopeRequired,
    TransientAuthError,
)
from mindos.infra.resilience import CircuitBreaker, RetryPolicy
from mindos.integrations.google import GoogleCalendar
from tests.support.auth_kit import MemoryStates
from tests.support.fakes import ManualClock

IST = ZoneInfo("Asia/Kolkata")
TIMED = {
    "id": "e1",
    "summary": "Standup",
    "start": {"dateTime": "2026-11-23T09:30:00+05:30", "timeZone": "Asia/Kolkata"},
    "end": {"dateTime": "2026-11-23T10:00:00+05:30", "timeZone": "Asia/Kolkata"},
    "attendees": [{"email": "a@example.com"}, {"displayName": "no email"}],
    "location": "Room 1",
}
ALL_DAY = {
    "id": "e2",
    "summary": "Holiday",
    "start": {"date": "2026-11-26"},
    "end": {"date": "2026-11-27"},
}


class Tokens:
    def __init__(self) -> None:
        self.issued = 0
        self.rejected_count = 0
        self.scopes: list[list[str]] = []

    async def access_token(
        self, provider: str, account_id: str, *, scopes: list[str] | tuple[str, ...] = ()
    ) -> AccessToken:
        self.issued += 1
        self.scopes.append(list(scopes))
        return AccessToken(f"tok{self.issued}", ManualClock().now() + timedelta(hours=1))

    async def rejected(self, provider: str, account_id: str) -> None:
        self.rejected_count += 1


class Api:
    """A scripted Google. Records requests; replays responses (the last one repeats)."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.script = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


class NoSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def ok(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def err(status: int, reason: str = "", **headers: str) -> httpx.Response:
    body = {"error": {"message": "nope", "errors": [{"reason": reason}]}}
    return httpx.Response(status, json=body, headers=headers)


async def calendar(
    *responses: httpx.Response | Exception, signed_in: bool = True
) -> tuple[GoogleCalendar, Api, Tokens, NoSleep]:
    api, tokens, sleep = Api(*responses), Tokens(), NoSleep()
    states = MemoryStates()
    if signed_in:
        await states.put("google", "me@x.com", AuthStatus.CONNECTED, ["s"])
    cal = GoogleCalendar(
        tokens=tokens,
        states=states,
        http=httpx.AsyncClient(transport=httpx.MockTransport(api)),
        tz=IST,
        policy=RetryPolicy(max_attempts=3, base_s=0.1, cap_s=1, deadline_s=60),
        breaker=CircuitBreaker(threshold=50),
        sleep=sleep,
    )
    return cal, api, tokens, sleep


DAY = (datetime(2026, 11, 23, tzinfo=IST), datetime(2026, 11, 24, tzinfo=IST))
DRAFT = EventDraft(
    title="Lunch",
    start=datetime(2026, 11, 26, 13, tzinfo=IST),
    end=datetime(2026, 11, 26, 14, tzinfo=IST),
    timezone="Asia/Kolkata",
    attendees=("b@example.com",),
)


# ---- reading ---------------------------------------------------------------------------------
async def test_search_sends_the_token_and_range_and_maps_timed_and_all_day_events() -> None:
    cal, api, tokens, _ = await calendar(ok({"items": [TIMED, ALL_DAY]}))
    events = await cal.search_events(start=DAY[0], end=DAY[1], query="stand", limit=10)
    request = api.requests[0]
    assert request.headers["Authorization"] == "Bearer tok1"
    assert request.url.params["q"] == "stand" and request.url.params["singleEvents"] == "true"
    assert request.url.params["timeMin"] == "2026-11-23T00:00:00+05:30"
    timed, all_day = events
    assert (timed.title, timed.attendees, timed.all_day) == ("Standup", ("a@example.com",), False)
    assert all_day.all_day and all_day.start == datetime(2026, 11, 26, tzinfo=IST)
    assert tokens.scopes[0][0].endswith("calendar.readonly")


async def test_get_event_returns_none_for_a_missing_event() -> None:
    cal, *_ = await calendar(err(404, "notFound"))
    assert await cal.get_event("gone") is None


async def test_not_signed_in_is_a_reauth_error_with_no_request() -> None:
    cal, api, *_ = await calendar(ok({}), signed_in=False)
    with pytest.raises(ReauthRequired, match="mindos auth google"):
        await cal.get_event("x")
    assert api.requests == []


# ---- auth and throttling ---------------------------------------------------------------------
async def test_a_401_refreshes_the_token_once_and_retries() -> None:
    cal, api, tokens, _ = await calendar(err(401), ok(TIMED))
    assert (await cal.get_event("e1")) is not None
    assert tokens.rejected_count == 1 and tokens.issued == 2
    assert [r.headers["Authorization"] for r in api.requests] == ["Bearer tok1", "Bearer tok2"]


async def test_two_401s_in_a_row_mean_reauth_not_a_loop() -> None:
    cal, api, *_ = await calendar(err(401))
    with pytest.raises(ReauthRequired):
        await cal.get_event("e1")
    assert len(api.requests) == 2


async def test_a_missing_scope_is_reported_with_the_scope_and_never_retried() -> None:
    cal, api, *_ = await calendar(err(403, "insufficientPermissions"))
    with pytest.raises(ScopeRequired) as caught:
        await cal.delete_event("e1", notify=False)
    assert caught.value.scope_delta[0].endswith("calendar.events") and len(api.requests) == 1


async def test_a_429_on_a_read_waits_for_retry_after_then_succeeds() -> None:
    cal, api, _, sleep = await calendar(err(429, "", **{"Retry-After": "5"}), ok(TIMED))
    assert await cal.get_event("e1") is not None
    assert len(api.requests) == 2 and sleep.delays[0] >= 5


async def test_a_5xx_on_a_read_is_retried_and_then_reported_as_transient() -> None:
    cal, api, *_ = await calendar(err(503))
    with pytest.raises(ProviderError) as caught:
        await cal.get_event("e1")
    assert caught.value.transient and len(api.requests) == 3


async def test_an_open_breaker_surfaces_as_a_transient_auth_error() -> None:
    cal, api, *_ = await calendar(err(503))
    cal._breaker = CircuitBreaker(threshold=1, reset_s=999)
    for _ in range(2):
        with pytest.raises(TransientAuthError):
            await cal.get_event("e1")
    assert len(api.requests) == 1  # the first failure opened it; nothing else was sent


# ---- writing ---------------------------------------------------------------------------------
async def test_create_sends_the_key_the_invitation_choice_and_maps_the_result() -> None:
    created = {**TIMED, "id": "new1", "summary": "Lunch"}
    cal, api, tokens, _ = await calendar(ok(created))
    event = await cal.create_event(DRAFT, request_key="key123", notify=True)
    assert event.id == "new1"
    request = api.requests[0]
    assert request.method == "POST" and request.url.params["sendUpdates"] == "all"
    body = api.body()
    assert body["summary"] == "Lunch" and body["attendees"] == [{"email": "b@example.com"}]
    assert body["extendedProperties"]["private"] == {"mindos_request_key": "key123"}
    assert body["start"] == {"dateTime": "2026-11-26T13:00:00+05:30", "timeZone": "Asia/Kolkata"}
    assert tokens.scopes[0][0].endswith("calendar.events")
    quiet, api2, *_ = await calendar(ok(created))
    await quiet.create_event(DRAFT, request_key="k", notify=False)
    assert api2.requests[0].url.params["sendUpdates"] == "none"


async def test_an_ambiguous_create_is_reconciled_by_key_and_not_repeated() -> None:
    made = {**TIMED, "id": "new1", "summary": "Lunch"}
    cal, api, *_ = await calendar(err(503), ok({"items": [made]}))
    event = await cal.create_event(DRAFT, request_key="key123", notify=True)
    assert event.id == "new1"
    methods = [r.method for r in api.requests]
    assert methods == ["POST", "GET"]  # one attempt, then a lookup: never a second POST
    assert api.requests[1].url.params["privateExtendedProperty"] == "mindos_request_key=key123"


async def test_an_ambiguous_create_that_really_failed_says_the_outcome_is_unknown() -> None:
    cal, api, *_ = await calendar(err(503), ok({"items": []}))
    with pytest.raises(ProviderError) as caught:
        await cal.create_event(DRAFT, request_key="key123", notify=True)
    assert caught.value.code == "outcome_unknown" and not caught.value.transient
    assert "may or may not" in str(caught.value)
    assert [r.method for r in api.requests] == ["POST", "GET"]


async def test_a_create_that_never_connected_is_retried_safely() -> None:
    created = {**TIMED, "id": "new1"}
    cal, api, *_ = await calendar(httpx.ConnectError("refused"), ok(created))
    assert (await cal.create_event(DRAFT, request_key="k", notify=True)).id == "new1"
    assert [r.method for r in api.requests] == ["POST", "POST"]  # it never left: safe


async def test_a_429_on_create_is_retried_because_nothing_was_created() -> None:
    cal, api, *_ = await calendar(err(429, ""), ok({**TIMED, "id": "n"}))
    assert (await cal.create_event(DRAFT, request_key="k", notify=True)).id == "n"
    assert len(api.requests) == 2


async def test_update_sends_only_the_changed_fields_and_none_means_missing() -> None:
    cal, api, *_ = await calendar(ok({**TIMED, "summary": "New"}), err(404, "notFound"))
    updated = await cal.update_event("e1", EventPatch(title="New", attendees=()), notify=False)
    assert updated is not None and updated.title == "New"
    assert api.body() == {"summary": "New", "attendees": []}
    assert api.requests[0].method == "PATCH" and api.requests[0].url.params["sendUpdates"] == "none"
    assert await cal.update_event("gone", EventPatch(title="x"), notify=False) is None


async def test_delete_reports_whether_anything_was_deleted() -> None:
    cal, api, *_ = await calendar(httpx.Response(204), httpx.Response(410))
    assert await cal.delete_event("e1", notify=True) is True
    assert await cal.delete_event("e1", notify=True) is False
    assert [r.method for r in api.requests] == ["DELETE", "DELETE"]


async def test_a_400_is_an_invalid_arguments_error_without_echoing_the_request() -> None:
    cal, *_ = await calendar(err(400, "invalid"))
    with pytest.raises(ProviderError) as caught:
        await cal.create_event(DRAFT, request_key="k", notify=True)
    assert caught.value.code == "invalid_arguments" and "b@example.com" not in str(caught.value)
