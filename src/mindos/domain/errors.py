"""Errors an integration adapter raises for the tool engine to report. Messages are safe to show."""

from __future__ import annotations


class ProviderError(Exception):
    """A provider refused or failed a request.

    `transient` means repeating the same request may succeed (rate limit, brief outage), and only
    the engine decides whether to: read tools are retried, side-effect tools never are.
    `code` is the machine-readable reason the model and the audit log see.
    """

    def __init__(self, message: str, *, code: str = "provider_error", transient: bool = False):
        super().__init__(message)
        self.code = code
        self.transient = transient
