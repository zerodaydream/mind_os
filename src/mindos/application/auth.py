"""Token management and the account lifecycle (ARCHITECTURE section 6).

`TokenManager` is what integration adapters ask for an access token. Access tokens live in memory
only; refresh tokens live in the Keychain and never leave this module. A refresh is single-flight:
an in-process lock per account, then a cross-process lock, so ten concurrent calls (or the app
and the worker) cause one refresh. Failures move the account's `AuthStatus`, and an account that
is not `CONNECTED` refuses calls without asking the provider again.

`AuthService` is the user's side: connect, revoke, and status (`mindos auth ...`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence

from mindos.domain import (
    AccessToken,
    AuthError,
    AuthStatus,
    Clock,
    IntegrationState,
    IntegrationStates,
    OAuthProvider,
    ReauthRequired,
    RefreshLock,
    ScopeRequired,
    SecretStore,
    TokenGrant,
)

log = logging.getLogger(__name__)

DEFAULT_ACCOUNT = "default"
_Key = tuple[str, str]


def refresh_key(provider: str, account_id: str) -> str:
    """The Keychain entry that holds this account's refresh token."""
    return f"refresh:{provider}:{account_id}"


class TokenManager:
    def __init__(
        self,
        *,
        providers: Mapping[str, OAuthProvider],
        secrets: SecretStore,
        states: IntegrationStates,
        lock: RefreshLock,
        clock: Clock,
        skew_s: float = 60.0,
    ) -> None:
        self._providers = providers
        self._secrets = secrets
        self._states = states
        self._lock = lock
        self._clock = clock
        self._skew_s = skew_s
        self._cache: dict[_Key, TokenGrant] = {}
        self._local: dict[_Key, asyncio.Lock] = {}

    async def access_token(
        self, provider: str, account_id: str, *, scopes: Sequence[str] = ()
    ) -> AccessToken:
        """A valid access token, refreshed ahead of expiry. Raises `AuthError` if it cannot."""
        key = (provider, account_id)
        hit = self._fresh(key, scopes)
        if hit is not None:
            return hit
        async with self._local.setdefault(key, asyncio.Lock()):
            hit = self._fresh(key, scopes)  # whoever held the lock may have refreshed already
            if hit is not None:
                return hit
            async with self._lock.hold(provider, account_id):
                return (await self._refresh(provider, account_id, scopes)).access

    async def rejected(self, provider: str, account_id: str) -> None:
        self._cache.pop((provider, account_id), None)

    def forget(self, provider: str, account_id: str) -> None:
        """Drop the in-memory token (after connect or revoke)."""
        self._cache.pop((provider, account_id), None)

    def _fresh(self, key: _Key, scopes: Sequence[str]) -> AccessToken | None:
        grant = self._cache.get(key)
        if grant is None or not set(scopes) <= set(grant.scopes):
            return None
        return grant.access if grant.access.fresh(self._clock.now(), self._skew_s) else None

    async def _refresh(self, provider: str, account_id: str, scopes: Sequence[str]) -> TokenGrant:
        state = await self._states.get(provider, account_id)
        self._refuse_if_unusable(provider, state, scopes)
        refresh_token = self._secrets.get(refresh_key(provider, account_id))
        if refresh_token is None:
            raise ReauthRequired(
                provider, f"{provider} is not signed in. Run: mindos auth {provider}"
            )
        granted = state.scopes if state else ()
        try:
            grant = await self._providers[provider].refresh(refresh_token)
        except ReauthRequired:
            self._cache.pop((provider, account_id), None)
            await self._states.put(provider, account_id, AuthStatus.NEEDS_REAUTH, granted)
            raise
        except ScopeRequired:
            await self._states.put(provider, account_id, AuthStatus.NEEDS_SCOPE, granted)
            raise
        if grant.refresh_token:  # some providers rotate it on every refresh
            self._secrets.set(refresh_key(provider, account_id), grant.refresh_token)
        new_scopes = grant.scopes or granted
        if state is None or state.scopes != new_scopes:
            await self._states.put(provider, account_id, AuthStatus.CONNECTED, new_scopes)
        self._cache[(provider, account_id)] = TokenGrant(grant.access, new_scopes)
        return self._cache[(provider, account_id)]

    @staticmethod
    def _refuse_if_unusable(
        provider: str, state: IntegrationState | None, scopes: Sequence[str]
    ) -> None:
        """Do not ask the provider again for an account already known to be unusable."""
        if state is None:
            return
        if state.status in (AuthStatus.NEEDS_REAUTH, AuthStatus.REVOKED):
            raise ReauthRequired(
                provider, f"{provider} needs you to sign in again. Run: mindos auth {provider}"
            )
        missing = tuple(s for s in scopes if s not in state.scopes)
        if state.status is AuthStatus.NEEDS_SCOPE or missing:
            raise ScopeRequired(
                provider,
                f"{provider} needs more permission. Run: mindos auth {provider}",
                missing,
            )


class AuthService:
    """Connect, revoke, and report on accounts. Used by `mindos auth`, never by the model."""

    def __init__(
        self,
        *,
        providers: Mapping[str, OAuthProvider],
        secrets: SecretStore,
        states: IntegrationStates,
        tokens: TokenManager,
    ) -> None:
        self._providers = providers
        self._secrets = secrets
        self._states = states
        self._tokens = tokens

    async def connect(self, provider: str, grant: TokenGrant) -> IntegrationState:
        """Store a completed sign-in. The refresh token goes to the Keychain, nowhere else."""
        if not grant.refresh_token:
            raise ReauthRequired(
                provider,
                "The provider returned no refresh token, so the sign-in cannot last. "
                f"Remove the app's access in your {provider} account settings and try again.",
            )
        account_id = grant.account_id or DEFAULT_ACCOUNT
        self._secrets.set(refresh_key(provider, account_id), grant.refresh_token)
        await self._states.put(provider, account_id, AuthStatus.CONNECTED, grant.scopes)
        self._tokens.forget(provider, account_id)
        return IntegrationState(
            provider=provider,
            account_id=account_id,
            status=AuthStatus.CONNECTED,
            scopes=grant.scopes,
        )

    async def revoke(self, provider: str, account_id: str = DEFAULT_ACCOUNT) -> bool:
        """Disconnect locally, always; ask the provider to revoke too. True if it did."""
        token = self._secrets.get(refresh_key(provider, account_id))
        client = self._providers.get(provider)  # unconfigured here: local removal still works
        revoked_remotely = False
        if token is not None and client is not None:
            try:
                revoked_remotely = await client.revoke(token)
            except AuthError:
                log.warning("%s did not confirm revocation; the local token is deleted", provider)
        self._secrets.delete(refresh_key(provider, account_id))
        await self._states.put(provider, account_id, AuthStatus.REVOKED, ())
        self._tokens.forget(provider, account_id)
        return revoked_remotely

    async def status(self) -> list[IntegrationState]:
        return await self._states.all()
