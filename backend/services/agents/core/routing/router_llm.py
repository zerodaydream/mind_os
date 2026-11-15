"""LLM routing when keywords or an isolated message are not enough."""

from __future__ import annotations

import re

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.render.prompt_budget import (
    CHAT_HISTORY_MSG_CHARS,
    CHAT_HISTORY_TURNS,
    DEEP_CHAT_HISTORY_TURNS,
    EXTRACT_MAX_OUTPUT_TOKENS,
    ROUTER_LLM_MAX_CHARS,
    clip_text,
)
from backend.services.agents.core.routing.router import Intent, classify

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

_CONTEXTIVE_MAX_CHARS = 120


def _format_history(history: list[dict] | None, *, max_turns: int | None = None) -> str:
    if not history:
        return ""
    limit = max_turns if max_turns is not None else CHAT_HISTORY_TURNS
    lines: list[str] = []
    for m in history[-limit:]:
        role = m.get("role") or "user"
        content = (m.get("content") or "").strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.append(f"{label}: {clip_text(content, CHAT_HISTORY_MSG_CHARS)}")
    return "\n".join(lines)


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


def _looks_like_thread_continuation(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return False
    if any(w in lower for w in ("remember", "my name is", "call me", "forget that", "who am i")):
        return False
    if lower in {"yes", "y", "ok", "okay", "sure", "done", "go ahead", "do it", "please do"}:
        return True
    return len(lower) <= 32


def thread_pending_execute(history: list[dict] | None) -> bool:
    if not history:
        return False
    for entry in history[-10:]:
        stored = entry.get("intent")
        if stored in ("execute", "plan"):
            return True
        if entry.get("role") != "user":
            continue
        text = (entry.get("content") or "").strip()
        if not text:
            continue
        routed, _ = classify(text)
        if routed in ("execute", "plan"):
            return True
    return False


def compact_thread_task(message: str, history: list[dict] | None) -> str:
    lines: list[str] = []
    for entry in (history or [])[-8:]:
        content = (entry.get("content") or "").strip()
        if not content:
            continue
        label = "User" if entry.get("role") == "user" else "Assistant"
        lines.append(f"{label}: {clip_text(content, CHAT_HISTORY_MSG_CHARS)}")
    lines.append(f"User: {message.strip()}")
    return "\n".join(lines)


def needs_turn_routing(message: str, intent: Intent, history: list[dict] | None) -> bool:
    if needs_llm_refine(message, intent):
        return True
    if not history:
        return False
    if intent != "converse":
        return False
    if len(message.strip()) <= _CONTEXTIVE_MAX_CHARS:
        return True
    return thread_pending_execute(history)


def _parse_intent_line(text: str) -> Intent | None:
    line = (text or "").strip().splitlines()[0].lower() if text else ""
    for candidate in ("execute", "plan", "converse"):
        if candidate in line:
            return candidate  # type: ignore[return-value]
    return None


def _parse_turn_routing(text: str, fallback_message: str) -> tuple[Intent, str] | None:
    intent = _parse_intent_line(text)
    if intent is None:
        return None
    task = fallback_message.strip()
    upper = (text or "").upper()
    if "TASK:" in upper:
        for line in (text or "").splitlines():
            if line.strip().upper().startswith("TASK:"):
                task = line.split(":", 1)[1].strip() or task
                break
    return intent, task


async def refine_intent(gateway, message: str) -> Intent | None:
    routed = await route_turn(gateway, message, None)
    return routed[0] if routed else None


async def route_turn(
    gateway,
    message: str,
    history: list[dict] | None,
) -> tuple[Intent, str] | None:
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return None
    hist = _format_history(history)
    parts = [
        "Classify the latest user message for MindOS using the conversation when needed.",
        "converse = Q&A over indexed notes/mail only, no multi-step work.",
        "plan = search/list/analyze indexed data, tables, job mail, multi-step research.",
        "execute = calendar events, workspace files, shell commands, integrations — actions that change things outside chat.",
        "Short replies (yes, ok, do it) follow the thread: keep plan/execute when the user is continuing or confirming an in-progress task.",
        "",
        "Reply in exactly this format:",
        "INTENT: converse|plan|execute",
        "TASK: what MindOS should do on this turn (full details; merge earlier turns when the latest line only confirms or adjusts)",
        "",
        f"Latest user message:\n{clip_text(message, ROUTER_LLM_MAX_CHARS)}",
    ]
    if hist:
        parts.insert(6, f"Recent conversation:\n{hist}\n")
    prompt = "\n".join(parts)
    try:
        res = await gateway.complete(
            prompt, task=LlmTask.EXTRACT, max_output_tokens=EXTRACT_MAX_OUTPUT_TOKENS
        )
        return _parse_turn_routing(res.text or "", message)
    except Exception:
        return None