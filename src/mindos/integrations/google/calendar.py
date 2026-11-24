"""Google Calendar over its REST API: `CalendarReader` and `CalendarWriter` (ARCHITECTURE 7, 11).

Tokens come from a `TokenSource`, never a refresh token. A 401 drops the cached token and retries
once; a 403 for a missing scope becomes `ScopeRequired` (not retried). Reads retry transient
failures with full jitter. Writes retry only what provably never reached Google (connection
refused, 429); anything ambiguous is reconciled instead of repeated: a create carries a private
extended property, so after an uncertain failure the adapter looks the event up by that key and
only reports failure if it is really absent.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from mindos.domain import (
    AuthStatus,
    CalendarEvent,
    EventDraft,
    EventPatch,
    IntegrationStates,
    ProviderError,
    ReauthRequired,
    ScopeRequired,
    TokenSource,
    TransientAuthError,
)
from mindos.infra.resilience import (
    CircuitBreaker,
    CircuitOpen,
    RetryPolicy,
    parse_retry_after,
    retry,
)

API = "https://www.googleapis.com/calendar/v3/calendars"
_G = "https://www.googleapis.com/auth/"
READ_SCOPE = f"{_G}calendar.readonly"
WRITE_SCOPE = f"{_G}calendar.events"
_KEY_PROPERTY = "mindos_request_key"
_SCOPE_REASONS = frozenset({"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_MESSAGE_CHARS = 200


class _Retryable(Exception):
    def __init__(self, status: int | None, retry_after: float | None = None) -> None:
        super().__init__(f"retryable ({status})")
        self.status = status
        self.retry_after = retry_after


class _Unsent(_Retryable):
    """The request never left this machine, so repeating it cannot duplicate anything."""


class GoogleCalendar:
    def __init__(
        self,
        *,
        tokens: TokenSource,
        states: IntegrationStates,
        http: httpx.AsyncClient,
        tz: ZoneInfo,
        calendar_id: str = "primary",
        policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._tokens = tokens
        self._states = states
        self._http = http
        self._tz = tz
        self._base = f"{API}/{calendar_id}/events"
        self._policy = policy or RetryPolicy()
        self._breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    # ---- CalendarReader ---------------------------------------------------------------------
    async def search_events(
        self, *, start: datetime, end: datetime, query: str | None, limit: int
    ) -> list[CalendarEvent]:
        params: dict[str, Any] = {
            "timeMin": start.isoformat(),
            "timeMax": end.isoformat(),
            "maxResults": min(limit, 250),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if query:
            params["q"] = query
        data = await self._call("GET", self._base, READ_SCOPE, params=params, safe_to_repeat=True)
        return [self._event(item) for item in (data or {}).get("items", [])][:limit]

    async def get_event(self, event_id: str) -> CalendarEvent | None:
        data = await self._call(
            "GET", f"{self._base}/{event_id}", READ_SCOPE, safe_to_repeat=True, missing_ok=True
        )
        return None if data is None else self._event(data)

    # ---- CalendarWriter ---------------------------------------------------------------------
    async def create_event(
        self, draft: EventDraft, *, request_key: str, notify: bool
    ) -> CalendarEvent:
        body = self._body(draft.model_dump())
        body["extendedProperties"] = {"private": {_KEY_PROPERTY: request_key}}
        try:
            data = await self._call(
                "POST", self._base, WRITE_SCOPE, json=body, params=self._notify(notify)
            )
        except ProviderError as exc:
            if exc.code != "outcome_unknown":
                raise
            found = await self.find_created(request_key)  # reconcile: did it happen after all?
            if found is None:
                raise
            return found
        assert data is not None
        return self._event(data)

    async def find_created(self, request_key: str) -> CalendarEvent | None:
        params = {"privateExtendedProperty": f"{_KEY_PROPERTY}={request_key}", "maxResults": 1}
        data = await self._call("GET", self._base, READ_SCOPE, params=params, safe_to_repeat=True)
        items = (data or {}).get("items", [])
        return self._event(items[0]) if items else None

    async def update_event(
        self, event_id: str, patch: EventPatch, *, notify: bool
    ) -> CalendarEvent | None:
        data = await self._call(
            "PATCH",
            f"{self._base}/{event_id}",
            WRITE_SCOPE,
            json=self._body(patch.model_dump(exclude_none=True)),
            params=self._notify(notify),
            missing_ok=True,
        )
        return None if data is None else self._event(data)

    async def delete_event(self, event_id: str, *, notify: bool) -> bool:
        data = await self._call(
            "DELETE",
            f"{self._base}/{event_id}",
            WRITE_SCOPE,
            params=self._notify(notify),
            missing_ok=True,
        )
        return data is not None

    # ---- HTTP -------------------------------------------------------------------------------
    async def _account(self) -> str:
        for state in await self._states.all():
            if state.provider == "google" and state.status is AuthStatus.CONNECTED:
                return state.account_id
        raise ReauthRequired("google", "Google is not signed in. Run: mindos auth google")

    async def _call(
        self,
        method: str,
        url: str,
        scope: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        safe_to_repeat: bool = False,
        missing_ok: bool = False,
    ) -> dict[str, Any] | None:
        """One API call. `safe_to_repeat` marks a read or lookup: any transient fault retries."""
        account = await self._account()
        for refreshed in (False, True):
            token = await self._tokens.access_token("google", account, scopes=[scope])
            response = await self._send(
                method, url, token.value, params=params, json=json, idempotent=safe_to_repeat
            )
            if response.status_code != 401 or refreshed:
                break
            await self._tokens.rejected("google", account)  # the token was bad: refresh, once
        return self._result(response, scope, missing_ok)

    async def _send(
        self,
        method: str,
        url: str,
        token: str,
        *,
        params: Mapping[str, Any] | None,
        json: Mapping[str, Any] | None,
        idempotent: bool,
    ) -> httpx.Response:
        async def once() -> httpx.Response:
            try:
                response = await self._http.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise _Unsent(None) from exc
            except httpx.TransportError as exc:  # sent, answer unknown
                raise _Retryable(None) from exc
            if response.status_code == 429 or response.status_code >= 500:
                raise _Retryable(response.status_code, _after(response))
            return response

        def worth_retrying(exc: Exception) -> bool:
            return isinstance(exc, _Unsent) or (idempotent and isinstance(exc, _Retryable))

        kwargs: dict[str, Any] = {} if self._sleep is None else {"sleep": self._sleep}
        try:
            return await retry(
                once,
                policy=self._policy,
                retryable=lambda e: worth_retrying(e) or _is_throttle(e),
                retry_after=lambda e: e.retry_after if isinstance(e, _Retryable) else None,
                breaker=self._breaker,
                **kwargs,
            )
        except CircuitOpen as exc:
            raise TransientAuthError(
                "google", "Google Calendar is failing; try again shortly."
            ) from exc
        except _Retryable as exc:
            transient = idempotent or exc.status == 429 or isinstance(exc, _Unsent)
            raise ProviderError(
                "Google Calendar did not answer."
                + ("" if transient else " The change may or may not have been made."),
                code="unavailable" if transient else "outcome_unknown",
                transient=transient,
            ) from exc

    def _result(
        self, response: httpx.Response, scope: str, missing_ok: bool
    ) -> dict[str, Any] | None:
        status = response.status_code
        if status in (200, 201):
            body = response.json()
            return body if isinstance(body, dict) else {}
        if status == 204:
            return {}
        if status in (404, 410) and missing_ok:
            return None
        message, reason = _error_of(response)
        if status == 401:
            raise ReauthRequired("google", "Google rejected the sign-in. Run: mindos auth google")
        if status == 403 and reason in _SCOPE_REASONS:
            raise ScopeRequired(
                "google", "Google Calendar needs more permission. Run: mindos auth google", (scope,)
            )
        if status == 403 and reason in _RATE_REASONS:
            raise ProviderError(
                "Google Calendar is rate limiting.", code="rate_limited", transient=True
            )
        if status in {404, 410}:
            raise ProviderError("That calendar or event was not found.", code="not_found")
        raise ProviderError(
            f"Google Calendar refused the request ({status}): {message}",
            code="invalid_arguments" if status == 400 else "provider_error",
        )

    # ---- mapping ----------------------------------------------------------------------------
    @staticmethod
    def _notify(notify: bool) -> dict[str, str]:
        return {"sendUpdates": "all" if notify else "none"}

    def _body(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        for name in ("location", "description"):
            if name in fields and fields[name] is not None:
                body[name] = fields[name]
        if "attendees" in fields:
            body["attendees"] = [{"email": email} for email in fields["attendees"]]
        for name in ("start", "end"):
            if name in fields and fields[name] is not None:
                body[name] = self._when(fields[name], bool(fields.get("all_day")))
        return body

    def _when(self, value: datetime, all_day: bool) -> dict[str, str]:
        if all_day:
            return {"date": value.astimezone(self._tz).date().isoformat()}
        return {"dateTime": value.isoformat(), "timeZone": str(self._tz)}

    def _event(self, item: Mapping[str, Any]) -> CalendarEvent:
        start, end = item.get("start", {}), item.get("end", {})
        zone = str(start.get("timeZone") or self._tz)
        return CalendarEvent(
            id=str(item["id"]),
            title=str(item.get("summary") or "(no title)"),
            start=self._instant(start),
            end=self._instant(end),
            timezone=zone,
            all_day="date" in start,
            location=item.get("location"),
            attendees=tuple(a["email"] for a in item.get("attendees", []) if "email" in a),
            description=item.get("description"),
        )

    def _instant(self, when: Mapping[str, Any]) -> datetime:
        if "dateTime" in when:
            parsed = datetime.fromisoformat(str(when["dateTime"]).replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return datetime.combine(date.fromisoformat(str(when["date"])), time.min, tzinfo=self._tz)


def _after(response: httpx.Response) -> float | None:
    return parse_retry_after(response.headers.get("Retry-After"))


def _is_throttle(exc: Exception) -> bool:
    return isinstance(exc, _Retryable) and exc.status == 429


def _error_of(response: httpx.Response) -> tuple[str, str]:
    """(message, reason) from Google's error body, bounded. Never echoes the request."""
    try:
        error = response.json().get("error", {})
        reason = (error.get("errors") or [{}])[0].get("reason", "")
        return str(error.get("message", ""))[:_MESSAGE_CHARS], str(reason)
    except (ValueError, AttributeError, IndexError):
        return "", ""
