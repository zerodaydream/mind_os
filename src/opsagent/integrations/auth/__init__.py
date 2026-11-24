"""OAuth sign-in and token endpoints for Google and Microsoft, and the Keychain store."""

from opsagent.integrations.auth.keychain import KeychainCredentialProvider, KeychainSecretStore
from opsagent.integrations.auth.login import sign_in
from opsagent.integrations.auth.loopback import LoopbackError, LoopbackReceiver
from opsagent.integrations.auth.oauth2 import OAuth2Client, OAuthEndpoints
from opsagent.integrations.auth.providers import (
    BASE_SCOPES,
    CAPABILITIES,
    GOOGLE,
    microsoft,
    scopes_for,
)

__all__ = [
    "BASE_SCOPES",
    "CAPABILITIES",
    "GOOGLE",
    "KeychainCredentialProvider",
    "KeychainSecretStore",
    "LoopbackError",
    "LoopbackReceiver",
    "OAuth2Client",
    "OAuthEndpoints",
    "microsoft",
    "scopes_for",
    "sign_in",
]
