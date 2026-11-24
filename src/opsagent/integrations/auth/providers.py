"""Google and Microsoft Entra as data: endpoints, and the scopes behind each capability.

A capability is what the user enables (`mail-send`); the scopes are the smallest set that allows
it. Sign-in asks for exactly the capabilities requested, and adding one later is incremental
consent (ARCHITECTURE 6.1). Microsoft Graph accepts delegated permissions on work or school
accounts only for Teams messaging.
"""

from __future__ import annotations

from types import MappingProxyType

from opsagent.integrations.auth.oauth2 import OAuthEndpoints

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

MICROSOFT_BASE = ("openid", "profile", "offline_access")
MICROSOFT_CAPABILITIES = MappingProxyType(
    {
        "teams-read": ("Chat.Read",),
        "teams-send": ("ChatMessage.Send", "ChannelMessage.Send"),
    }
)


def microsoft(tenant: str = "organizations") -> OAuthEndpoints:
    """Entra v2 endpoints. `organizations` admits work and school accounts, never personal ones."""
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return OAuthEndpoints(
        name="microsoft",
        authorize_url=f"{base}/authorize",
        token_url=f"{base}/token",
        revoke_url=None,  # Entra v2 has no token-revocation endpoint; removal is local
        authorize_params={"prompt": "select_account"},
        account_claims=("preferred_username", "email"),
    )


CAPABILITIES = MappingProxyType(
    {"google": GOOGLE_CAPABILITIES, "microsoft": MICROSOFT_CAPABILITIES}
)
BASE_SCOPES = MappingProxyType({"google": GOOGLE_BASE, "microsoft": MICROSOFT_BASE})


def scopes_for(provider: str, capabilities: list[str]) -> tuple[str, ...]:
    """Base scopes plus those of each capability. Unknown names raise `KeyError`."""
    known = CAPABILITIES[provider]
    unknown = [c for c in capabilities if c not in known]
    if unknown:
        raise KeyError(f"unknown {provider} capability: {', '.join(unknown)}")
    scopes = list(BASE_SCOPES[provider])
    for capability in capabilities:
        scopes.extend(s for s in known[capability] if s not in scopes)
    return tuple(scopes)
