"""Result normalization (ARCHITECTURE section 4.2, stage 9).

Order: serialize, redact, offload if oversized, tag as untrusted. Untrusted tagging is last so
the closing tag is never cut off by the size bound.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from opsagent.domain import ArtifactStore, redact

PREVIEW_CHARS = 160
_OPEN = "<untrusted"
_CLOSE = "</untrusted>"


def serialize(value: Any) -> str:
    """Text form of a handler result. Secrets are masked before anything else sees it."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    value = redact(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def wrap_untrusted(text: str, source: str) -> str:
    """Label text as data written by someone else. A forged closing tag is defused."""
    safe = text.replace(_CLOSE, "<\\/untrusted>").replace(_OPEN, "<\\untrusted")
    return f'{_OPEN} source="{source}">\n{safe}\n{_CLOSE}'


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


async def bound(text: str, *, limit: int, store: ArtifactStore) -> tuple[str, str | None]:
    """Fit `text` into `limit` characters. Returns (model-facing text, artifact ID or None).

    An oversized result is stored whole and replaced by a head plus a pointer to `read_artifact`.
    """
    if len(text) <= limit:
        return text, None
    artifact_id = await store.put(text, media_type="text/plain")
    head = text[:limit]
    note = (
        f"\n[truncated: showing {len(head)} of {len(text)} characters. "
        f'Call read_artifact with artifact_id="{artifact_id}" and offset={len(head)} for more.]'
    )
    return head + note, artifact_id
