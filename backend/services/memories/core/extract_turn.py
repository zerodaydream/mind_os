"""Post-turn memory extractor — off the hot path, user lines only."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import Memory
from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.memories.core.rules import extract_memory_rules

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.I)

_EXTRACT_PROMPT = """Extract durable personal memories from the USER message only.
Return JSON array of objects: [{"kind":"preference|fact|decision|person|procedure","content":"one sentence"}]
Empty array if nothing durable. Never invent facts. Never extract from assistant text.
User message:
"""


async def extract_memories_after_turn(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    user_message: str,
    gateway=None,
) -> int:
    """Extract and insert proposed memories from the user line. Returns insert count."""
    text = (user_message or "").strip()
    if len(text) < 8:
        return 0

    drafts: list[dict[str, Any]] = []
    if gateway is not None and gateway.mode != LlmMode.DEGRADED:
        try:
            res = await gateway.complete(
                _EXTRACT_PROMPT + text[:2000],
                task=LlmTask.EXTRACT,
                max_output_tokens=256,
            )
            drafts = _parse_drafts(res.text or "")
        except Exception:
            logger.warning("memory_extract_llm_failed", exc_info=True)
    if not drafts:
        drafts = extract_memory_rules(text)
        for d in drafts:
            d["origin"] = "chat_derived"
            d["approved"] = False

    if not drafts:
        return 0

    existing_rows = await session.execute(
        select(Memory.kind, Memory.content).where(Memory.user_id == user_id)
    )
    existing = {(k, c.lower()) for k, c in existing_rows.all()}
    created = 0
    for draft in drafts:
        content = str(draft.get("content") or "").strip()[:500]
        kind = str(draft.get("kind") or "fact").strip().lower() or "fact"
        if not content or (kind, content.lower()) in existing:
            continue
        session.add(
            Memory(
                user_id=user_id,
                kind=kind,
                content=content,
                source=str(draft.get("origin") or "chat_derived"),
                approved=False,
                evidence=[{"origin": "chat_turn"}],
            )
        )
        existing.add((kind, content.lower()))
        created += 1
    return created


def _parse_drafts(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        out.append(
            {
                "kind": str(item.get("kind") or "fact"),
                "content": content,
                "approved": False,
                "origin": "chat_derived",
            }
        )
    return out
