"""Authentication contracts: integration status, tokens, errors, and ports (ARCHITECTURE section 6).

Pure types. No secret is ever stored in a field that is printed: `AccessToken` and `TokenGrant`
hide their values from `repr`, so a log line or a traceback cannot leak one.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class AuthStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"  # the refresh token no longer works; sign in again
    NEEDS_SCOPE = "NEEDS_SCOPE"  # signed in, but a needed permission was never granted
    REVOKED = "REVOKED"  # the user disconnected it


class IntegrationState(BaseModel):
    """Where one account stands. Holds no secret: tokens live in the Keychain."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    account_id: str
    status: AuthStatus
    scopes: tuple[str, ...] = ()
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AccessToken:
    """A short-lived bearer token. It exists in memory only."""

    value: str = field(repr=False)
    expires_at: datetime

    def fresh(self, now: datetime, skew_s: float) -> bool:
        return (self.expires_at - now).total_seconds() > skew_s


@dataclass(frozen=True)
class TokenGrant:
    """What a token endpoint returned. `refresh_token` is None if the provider did not rotate it."""

    access: AccessToken
    scopes: tuple[str, ...]
    refresh_token: str | None = field(default=None, repr=False)
    account_id: str | None = None  # from the ID token, when the sign-in returned one


# ---- errors ----------------------------------------------------------------------------------
class AuthError(Exception, ABC):
    """An authentication problem the user must or may fix. The message is safe to show."""

    code = "auth_error"

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(message)
        self.provider = provider


class ReauthRequired(AuthError):
    """`invalid_grant`, revoked, or never signed in. Never retried."""

    code = "auth_required"


class ScopeRequired(AuthError):
    """Signed in without a permission this call needs. Never retried."""

    code = "needs_scope"

    def __init__(self, provider: str, message: str, scope_delta: Sequence[str] = ()) -> None:
        super().__init__(provider, message)
        self.scope_delta = tuple(scope_delta)


class ConsentRequired(AuthError):
    """The tenant needs an administrator to approve the app. Disable the integration."""

    code = "consent_required"


class TransientAuthError(AuthError):
    """Network, 5xx, or rate limit that outlasted the retries, or an open circuit breaker."""

    code = "auth_unavailable"


# ---- ports -----------------------------------------------------------------------------------
class SecretStore(Protocol):
    """Where refresh tokens and API keys live (the macOS Keychain). Never the database."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class IntegrationStates(Protocol):
    async def get(self, provider: str, account_id: str) -> IntegrationState | None: ...

    async def put(
        self, provider: str, account_id: str, status: AuthStatus, scopes: Sequence[str]
    ) -> None: ...

    async def all(self) -> list[IntegrationState]: ...


class RefreshLock(Protocol):
    """Serializes a refresh across processes (the interactive app and the worker)."""

    def hold(self, provider: str, account_id: str) -> AbstractAsyncContextManager[None]: ...


class OAuthProvider(Protocol):
    """One identity provider's token endpoint. Raises `AuthError` subclasses, never raw HTTP."""

    name: str

    async def refresh(self, refresh_token: str) -> TokenGrant: ...

    async def revoke(self, token: str) -> bool:
        """Ask the provider to revoke. False if it has no revocation endpoint."""
        ...


class TokenSource(Protocol):
    """What an integration adapter asks for a token. It never sees a refresh token."""

    async def access_token(
        self, provider: str, account_id: str, *, scopes: Sequence[str] = ()
    ) -> AccessToken: ...

    async def rejected(self, provider: str, account_id: str) -> None:
        """The provider answered 401 to this token: forget it so the next call refreshes."""
        ...
