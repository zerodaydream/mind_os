"""Sortable unique IDs (ULID layout: 48-bit millisecond timestamp + 80 random bits)."""

from __future__ import annotations

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    """Return `<prefix>_<26-char ULID>`. IDs created later sort after earlier ones."""
    millis = time.time_ns() // 1_000_000
    entropy = int.from_bytes(os.urandom(10), "big")
    return f"{prefix}_{_encode(millis, 10)}{_encode(entropy, 16)}"
