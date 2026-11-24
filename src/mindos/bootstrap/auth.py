"""Wires the auth lifecycle: Keychain, OAuth clients, token manager, and the database state."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from opsagent.application import AuthService, TokenManager
from opsagent.domain import Clock, OAuthProvider
from opsagent.infra import Settings
from opsagent.integrations.auth import GOOGLE, KeychainSecretStore, OAuth2Client, microsoft
from opsagent.persistence import AdvisoryRefreshLock, IntegrationStateRepository
from opsagent.persistence.db import Pool

_HTTP_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class AuthKit:
    service: AuthService
    tokens: TokenManager
    clients: dict[str, OAuth2Client]  # only providers whose client ID is configured
    secrets: KeychainSecretStore
    http: httpx.AsyncClient


def build_auth(settings: Settings, pool: Pool, clock: Clock) -> AuthKit:
    """Providers without a configured client ID are left out; asking for one reports why."""
    http = httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=False)
    clients: dict[str, OAuth2Client] = {}
    if settings.google_client_id:
        secret = settings.google_client_secret
        clients["google"] = OAuth2Client(
            GOOGLE,
            client_id=settings.google_client_id,
            client_secret=secret.get_secret_value() if secret else None,
            clock=clock,
            http=http,
        )
    if settings.ms_client_id:
        clients["microsoft"] = OAuth2Client(
            microsoft(settings.ms_tenant), client_id=settings.ms_client_id, clock=clock, http=http
        )
    providers: dict[str, OAuthProvider] = dict(clients)
    secrets = KeychainSecretStore()
    states = IntegrationStateRepository(pool)
    tokens = TokenManager(
        providers=providers,
        secrets=secrets,
        states=states,
        lock=AdvisoryRefreshLock(pool),
        clock=clock,
    )
    service = AuthService(providers=providers, secrets=secrets, states=states, tokens=tokens)
    return AuthKit(service=service, tokens=tokens, clients=clients, secrets=secrets, http=http)
