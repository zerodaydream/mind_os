"""Credential adapters. The Keychain adapter arrives with Phase 5 (auth)."""

from __future__ import annotations

from mindos.domain import GEMINI_API_KEY
from mindos.infra.config import Settings


class SettingsCredentialProvider:
    """Dev-only fallback that serves secrets loaded into `Settings`."""

    def __init__(self, settings: Settings) -> None:
        self._values: dict[str, str] = {}
        if settings.gemini_api_key is not None:
            self._values[GEMINI_API_KEY] = settings.gemini_api_key.get_secret_value()

    def get(self, name: str) -> str | None:
        return self._values.get(name)
