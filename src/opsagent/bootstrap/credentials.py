"""Credential lookup across several sources."""

from __future__ import annotations

from opsagent.domain import CredentialProvider


class ChainedCredentials:
    """The first source that has the secret answers. Earlier sources win."""

    def __init__(self, *sources: CredentialProvider) -> None:
        self._sources = sources

    def get(self, name: str) -> str | None:
        for source in self._sources:
            value = source.get(name)
            if value is not None:
                return value
        return None
