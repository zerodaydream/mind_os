"""Secret redaction for logs, audit bodies, and event previews.

Two layers: key names that imply a secret, and value shapes of known credential formats.
Redaction is a safety net; secrets should not reach these paths in the first place.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|authorization|credential|private[_-]?key)",
    re.IGNORECASE,
)

_SECRET_VALUE = re.compile(
    "|".join(
        [
            r"hf_[A-Za-z0-9]{20,}",
            r"AIza[0-9A-Za-z_\-]{30,}",
            r"AQ\.[A-Za-z0-9_\-]{20,}",
            r"GOCSPX-[A-Za-z0-9_\-]{10,}",
            r"lsv2_[a-z]{2}_[a-f0-9]{16,}_[a-f0-9]+",
            r"sk-[A-Za-z0-9_\-]{20,}",
            r"ya29\.[A-Za-z0-9_\-]{20,}",
            r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
            r"(?i:bearer)\s+[A-Za-z0-9._~+/=\-]{16,}",
        ]
    )
)


def redact_text(text: str) -> str:
    return _SECRET_VALUE.sub(REDACTED, text)


def redact(value: Any) -> Any:
    """Return a copy of `value` with secrets masked. Handles nested dicts, lists, and strings."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            k: REDACTED if isinstance(k, str) and _SECRET_KEY.search(k) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact(v) for v in value]
    return value
