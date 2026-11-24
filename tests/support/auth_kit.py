"""In-memory auth ports and a scripted token endpoint (httpx.MockTransport)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx

from opsagent.application import TokenManager
from opsagent.domain import (
    AccessToken,
    AuthStatus,
    IntegrationState,
    OAuthProvider,
    TokenGrant,
)
from opsagent.infra.resilience import RetryPolicy
from opsagent.integrations.auth import GOOGLE, OAuth2Client
from tests.support.fakes import ManualClock

REFRESH = "rt-SECRET-refresh-value"
ACCESS = "at-SECRET-access-value"


class MemorySecrets:
    def __init__(self, **initial: str) -> None:
        self.data: dict[str, str] = dict(initial)

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> None:
        self.data[key] = value

    def delete(self, key: str) -> None:
        self.data.pop(key, None)


class MemoryStates:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], IntegrationState] = {}
        self.writes = 0

    async def get(self, provider: str, account_id: str) -> IntegrationState | None:
        return self.rows.get((provider, account_id))

    async def put(
        self, provider: str, account_id: str, status: AuthStatus, scopes: Sequence[str]
    ) -> None:
        self.writes += 1
        self.rows[(provider, account_id)] = IntegrationState(
            provider=provider, account_id=account_id, status=status, scopes=tuple(scopes)
        )

    async def all(self) -> list[IntegrationState]:
        return list(self.rows.values())


class LocalLock:
    """Stands in for the Postgres advisory lock: one holder at a time, across 'processes'."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.holds = 0

    def hold(self, provider: str, account_id: str) -> AbstractAsyncContextManager[None]:
        @asynccontextmanager
        async def held() -> Any:
            async with self._lock:
                self.holds += 1
                yield

        return held()


def id_token(email: str) -> str:
    def part(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part({'email': email})}.sig"


def token_json(**extra: Any) -> dict[str, Any]:
    return {
        "access_token": ACCESS,
        "expires_in": 3600,
        "scope": "openid email https://www.googleapis.com/auth/gmail.readonly",
        **extra,
    }


Handler = Callable[[httpx.Request], httpx.Response]


class Endpoint:
    """A scripted token endpoint. Records every request body it received."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.script = list(responses)
        self.calls: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(dict(httpx.QueryParams(request.content.decode())))
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def json_response(status: int, body: dict[str, Any], **headers: str) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


class NoSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def client_for(
    endpoint: Endpoint, clock: ManualClock, sleep: NoSleep | None = None
) -> OAuth2Client:
    return OAuth2Client(
        GOOGLE,
        client_id="cid",
        clock=clock,
        http=httpx.AsyncClient(transport=httpx.MockTransport(endpoint)),
        policy=RetryPolicy(max_attempts=3, base_s=0.1, cap_s=1.0, deadline_s=60),
        sleep=sleep or NoSleep(),
    )


class Rig:
    """A TokenManager over a real OAuth2Client and a scripted endpoint."""

    def __init__(self, *responses: httpx.Response | Exception, connected: bool = True) -> None:
        self.clock = ManualClock()
        self.endpoint = Endpoint(*responses)
        self.sleep = NoSleep()
        self.secrets = MemorySecrets(**({"refresh:google:me@x.com": REFRESH} if connected else {}))
        self.states = MemoryStates()
        self.lock = LocalLock()
        self.client = client_for(self.endpoint, self.clock, self.sleep)
        providers: dict[str, OAuthProvider] = {"google": self.client}
        self.tokens = TokenManager(
            providers=providers,
            secrets=self.secrets,
            states=self.states,
            lock=self.lock,
            clock=self.clock,
        )

    async def connected(self, *scopes: str) -> None:
        await self.states.put("google", "me@x.com", AuthStatus.CONNECTED, scopes)


def grant(refresh: str | None = REFRESH, account: str | None = "me@x.com") -> TokenGrant:
    clock = ManualClock()
    return TokenGrant(
        access=AccessToken(ACCESS, clock.now()),
        scopes=("openid",),
        refresh_token=refresh,
        account_id=account,
    )
