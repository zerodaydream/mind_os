"""Plain text, JSON, and XML attachment bodies."""

from __future__ import annotations


def extract_text_plain(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")