"""Optional LLM pass when keyword router returns converse on agent-like asks."""

from __future__ import annotations

import re

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.prompt_budget import (
    EXTRACT_MAX_OUTPUT_TOKENS,
    ROUTER_LLM_MAX_CHARS,
    clip_text,
)
from backend.services.agents.core.router import Intent

_AMBIGUOUS_ACTION = (
    "find",
    "list",
    "show",
    "search",
    "table",
    "gather",
    "sync",
    "read",
    "applied",
    "indeed",
    "gmail",
)


def needs_llm_refine(message: str, intent: Intent) -> bool:
    if intent != "converse":
        return False
    lower = message.lower()
    if re.search(r"(?m)^\s*\d+\.\s", message):
        return True
    if len(message) > 160 and any(a in lower for a in _AMBIGUOUS_ACTION):
        return True
    if "table" in lower and any(a in lower for a in ("subject", "content", "message", "mail")):
        return True
    return False


def _parse_intent_line(text: str) -> Intent | None:
    line = (text or "").strip().splitlines()[0].lower() if text else ""
    for candidate in ("execute", "plan", "converse"):
        if candidate in line:
            return candidate  # type: ignore[return-value]
    return None


async def refine_intent(gateway, message: str) -> Intent | None:
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return None
    prompt = (
        "Classify this user message into exactly one intent for MindOS.\n"
        "converse = Q&A over indexed notes/mail only, no multi-step work.\n"
        "plan = search/list/analyze indexed data, tables, job mail, multi-step research.\n"
        "execute = edit files, run commands, implement code in the workspace.\n"
        "Reply with ONE word only: converse, plan, or execute.\n\n"
        f"Message:\n{clip_text(message, ROUTER_LLM_MAX_CHARS)}"
    )
    try:
        res = await gateway.complete(
            prompt, task=LlmTask.EXTRACT, max_output_tokens=EXTRACT_MAX_OUTPUT_TOKENS
        )
        return _parse_intent_line(res.text or "")
    except Exception:
        return None
