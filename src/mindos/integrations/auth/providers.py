"""Google as data: endpoints, and the scopes behind each capability.

A capability is what the user enables (`mail-send`); the scopes are the smallest set that allows
it. Sign-in asks for exactly the capabilities requested, and adding one later is incremental
consent (ARCHITECTURE 6.1). Another provider is another entry here.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from mindos.integrations.auth.oauth2 import OAuthEndpoints

GOOGLE = OAuthEndpoints(
    name="google",
    authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    revoke_url="https://oauth2.googleapis.com/revoke",
    # offline + consent make Google issue a refresh token; granted scopes accumulate.
    authorize_params={
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    },
    account_claims=("email",),
)

_G = "https://www.googleapis.com/auth/"
GOOGLE_BASE = ("openid", "email")
GOOGLE_CAPABILITIES = MappingProxyType(
    {
        "mail-read": (f"{_G}gmail.readonly",),
        "mail-compose": (f"{_G}gmail.compose",),
        "mail-send": (f"{_G}gmail.send",),
        "calendar-read": (f"{_G}calendar.readonly",),
        "calendar-write": (f"{_G}calendar.events",),
    }
)

CAPABILITIES = MappingProxyType({"google": GOOGLE_CAPABILITIES})
BASE_SCOPES = MappingProxyType({"google": GOOGLE_BASE})


def scopes_for(provider: str, capabilities: Sequence[str]) -> tuple[str, ...]:
    """Base scopes plus those of each capability. Unknown names raise `KeyError`."""
    known = CAPABILITIES[provider]
    unknown = [c for c in capabilities if c not in known]
    if unknown:
        raise KeyError(f"unknown {provider} capability: {', '.join(unknown)}")
    scopes = list(BASE_SCOPES[provider])
    for capability in capabilities:
        scopes.extend(s for s in known[capability] if s not in scopes)
    return tuple(scopes)
