"""The Keychain store against an in-memory keyring backend (the real Keychain is never touched)."""

from __future__ import annotations

from collections.abc import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from mindos.bootstrap.credentials import ChainedCredentials
from mindos.domain import GEMINI_API_KEY
from mindos.integrations.auth import KeychainCredentialProvider, KeychainSecretStore


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.data:
            raise PasswordDeleteError("missing")
        del self.data[(service, username)]


@pytest.fixture
def backend() -> Iterator[MemoryKeyring]:
    previous = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(previous)


def test_secrets_round_trip_under_one_service_and_deleting_twice_is_fine(
    backend: MemoryKeyring,
) -> None:
    store = KeychainSecretStore()
    assert store.get("refresh:google:a") is None
    store.set("refresh:google:a", "token")
    assert store.get("refresh:google:a") == "token"
    assert list(backend.data) == [("mindos", "refresh:google:a")]
    store.delete("refresh:google:a")
    store.delete("refresh:google:a")  # already gone
    assert store.get("refresh:google:a") is None


def test_the_keychain_wins_over_the_dev_fallback(backend: MemoryKeyring) -> None:
    class Fallback:
        def get(self, name: str) -> str | None:
            return "from-env"

    chain = ChainedCredentials(KeychainCredentialProvider(KeychainSecretStore()), Fallback())
    assert chain.get(GEMINI_API_KEY) == "from-env"  # nothing stored yet
    KeychainSecretStore().set(GEMINI_API_KEY, "from-keychain")
    assert chain.get(GEMINI_API_KEY) == "from-keychain"
