"""OAuth sign-in and token endpoints for Google, and the Keychain store."""

from mindos.integrations.auth.keychain import KeychainCredentialProvider, KeychainSecretStore
from mindos.integrations.auth.login import sign_in
from mindos.integrations.auth.loopback import LoopbackError, LoopbackReceiver
from mindos.integrations.auth.oauth2 import OAuth2Client, OAuthEndpoints
from mindos.integrations.auth.providers import (
    BASE_SCOPES,
    CAPABILITIES,
    GOOGLE,
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
    "scopes_for",
    "sign_in",
]
