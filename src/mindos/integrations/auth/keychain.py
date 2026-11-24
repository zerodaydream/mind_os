"""macOS Keychain storage through `keyring`. The only place a refresh token or API key rests."""

from __future__ import annotations

import contextlib

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "mindos"


class KeychainSecretStore:
    """`SecretStore` over the system keyring, under one service name."""

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service

    def get(self, key: str) -> str | None:
        return keyring.get_password(self._service, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(self._service, key, value)

    def delete(self, key: str) -> None:
        with contextlib.suppress(PasswordDeleteError):  # already gone is fine
            keyring.delete_password(self._service, key)


class KeychainCredentialProvider:
    """`CredentialProvider` that reads named secrets (the Gemini key) from the Keychain."""

    def __init__(self, store: KeychainSecretStore) -> None:
        self._store = store

    def get(self, name: str) -> str | None:
        return self._store.get(name)
