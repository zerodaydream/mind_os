"""One OAuth 2.0 authorization-code + PKCE client for any public-client provider.

Providers differ in endpoints, scopes, and a few error codes, so each is configured data over
this class (ADR 0006). It never logs or raises a token, a code, or a request
body. Transient failures (network, 429, 5xx) are retried with full jitter and `Retry-After`, behind
a circuit breaker; a definite answer from the provider is never retried.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from mindos.domain import (
    AccessToken,
    AuthError,
    Clock,
    ConsentRequired,
    ReauthRequired,
    ScopeRequired,
    TokenGrant,
    TransientAuthError,
)
from mindos.infra.resilience import (
    CircuitBreaker,
    CircuitOpen,
    RetryPolicy,
    parse_retry_after,
    retry,
)

_CONSENT_CODES = frozenset({"consent_required", "admin_policy_enforced"})


@dataclass(frozen=True)
class OAuthEndpoints:
    name: str
    authorize_url: str
    token_url: str
    revoke_url: str | None
    authorize_params: Mapping[str, str] = field(default_factory=dict)
    account_claims: tuple[str, ...] = ("email",)  # ID-token claims that name the account


class _Transient(Exception):
    def __init__(self, status: int | None, retry_after: float | None = None) -> None:
        super().__init__(f"transient ({status})")
        self.retry_after = retry_after


class OAuth2Client:
    def __init__(
        self,
        endpoints: OAuthEndpoints,
        *,
        client_id: str,
        clock: Clock,
        http: httpx.AsyncClient,
        client_secret: str | None = None,
        policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.name = endpoints.name
        self._ep = endpoints
        self._client_id = client_id
        self._secret = client_secret
        self._clock = clock
        self._http = http
        self._policy = policy or RetryPolicy()
        self._breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    # ---- interactive sign-in ----------------------------------------------------------------
    def authorization_url(
        self,
        *,
        redirect_uri: str,
        scopes: Sequence[str],
        state: str,
        challenge: str,
        login_hint: str | None = None,
    ) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            **self._ep.authorize_params,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{self._ep.authorize_url}?{urlencode(params)}"

    async def exchange(self, *, code: str, verifier: str, redirect_uri: str) -> TokenGrant:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            }
        )

    # ---- OAuthProvider ----------------------------------------------------------------------
    async def refresh(self, refresh_token: str) -> TokenGrant:
        return await self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def revoke(self, token: str) -> bool:
        if self._ep.revoke_url is None:
            return False
        response = await self._post(self._ep.revoke_url, {"token": token})
        if response.status_code >= 400 and response.status_code != 400:
            raise AuthError(
                self.name, f"{self.name} refused the revocation ({response.status_code})."
            )
        return True  # 400 means the token was already invalid: nothing left to revoke

    # ---- plumbing ---------------------------------------------------------------------------
    async def _token_request(self, form: dict[str, str]) -> TokenGrant:
        body = {"client_id": self._client_id, **form}
        if self._secret:
            body["client_secret"] = self._secret
        response = await self._post(self._ep.token_url, body)
        if response.status_code != 200:
            raise self._error_for(response)
        return self._grant_from(self._json(response))

    async def _post(self, url: str, form: dict[str, str]) -> httpx.Response:
        async def once() -> httpx.Response:
            try:
                response = await self._http.post(url, data=form)
            except httpx.TransportError as exc:
                raise _Transient(None) from exc
            if response.status_code == 429 or response.status_code >= 500:
                raise _Transient(
                    response.status_code, parse_retry_after(response.headers.get("Retry-After"))
                )
            return response

        kwargs: dict[str, Any] = {} if self._sleep is None else {"sleep": self._sleep}
        try:
            return await retry(
                once,
                policy=self._policy,
                retryable=lambda e: isinstance(e, _Transient),
                retry_after=lambda e: e.retry_after if isinstance(e, _Transient) else None,
                breaker=self._breaker,
                **kwargs,
            )
        except (_Transient, CircuitOpen) as exc:
            raise TransientAuthError(
                self.name, f"{self.name} sign-in service is unavailable; try again shortly."
            ) from exc

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _error_for(self, response: httpx.Response) -> AuthError:
        data = self._json(response)
        code = str(data.get("error", ""))
        if code in _CONSENT_CODES:
            return ConsentRequired(
                self.name, f"{self.name} needs an administrator to approve this app for your org."
            )
        if code == "invalid_grant":
            return ReauthRequired(
                self.name,
                f"{self.name} no longer accepts this sign-in. Run: mindos auth {self.name}",
            )
        if code == "invalid_scope":
            return ScopeRequired(self.name, f"{self.name} did not grant a requested permission.")
        return AuthError(
            self.name, f"{self.name} refused the request: {code or response.status_code}."
        )

    def _grant_from(self, data: dict[str, Any]) -> TokenGrant:
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise AuthError(self.name, f"{self.name} returned no access token.")
        lifetime = int(data.get("expires_in", 3600))
        refresh = data.get("refresh_token")
        return TokenGrant(
            access=AccessToken(token, self._clock.now() + timedelta(seconds=lifetime)),
            scopes=tuple(str(data.get("scope", "")).split()),
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            account_id=self._account_from(data.get("id_token")),
        )

    def _account_from(self, id_token: object) -> str | None:
        """Read the account from the ID token. It came straight from the token endpoint over TLS."""
        if not isinstance(id_token, str) or id_token.count(".") != 2:
            return None
        segment = id_token.split(".")[1]
        try:
            claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        except ValueError:
            return None
        for name in self._ep.account_claims:
            value = claims.get(name) if isinstance(claims, dict) else None
            if isinstance(value, str) and value:
                return value.lower()
        return None
