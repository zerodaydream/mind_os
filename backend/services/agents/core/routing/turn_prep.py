"""One LLM call per turn: scope, intent, task, search query, prefetch flags."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from backend.common.utils.enums import LlmMode, LlmTask
from backend.services.agents.core.render.prompt_budget import (
    CHAT_HISTORY_MSG_CHARS,
    CHAT_HISTORY_TURNS,
    ROUTER_HISTORY_TURNS,
    ROUTER_LLM_MAX_CHARS,
    ROUTER_MAX_OUTPUT_TOKENS,
    clip_text,
)
from backend.services.agents.core.routing.router import (
    Intent,
    thread_pending_execute,
)

logger = logging.getLogger(__name__)

ScopeBlock = Literal["sorry", "clarify"]

_GREETING_START = re.compile(
    r"^(hi|hello|hey|yo|good\s+(morning|afternoon|evening)|thanks|thank\s+you|bye)\b",
    re.I,
)
_HTTP_URL = re.compile(r"https?://\S+", re.I)
_WORKSPACE_HINT = re.compile(
    r"\b(mail|gmail|inbox|reply|replies|calendar|calender|meeting|meetings|"
    r"reminder|file|drive|pdf|mindos|commitment|todo|workspace|attachment|"
    r"sync|outbox|owe|owing|owes|delete|schedule)\b",
    re.I,
)
_VAGUE_ANAPHORA = re.compile(
    r"\b(that|this|those|it|them|more|details?|link|above)\b",
    re.I,
)
# Write-oriented verbs only. Bare "calendar" / "create" must not force the agent
# lane ("what's on my calendar", "create a summary").
_EXECUTE_VERBS = re.compile(
    r"\b(schedule|delete|rename|sync)\b|"
    r"\b(create|update|cancel)\b.{0,48}\b(event|meeting|appointment|invite|calendar)\b|"
    r"\b(event|meeting|appointment|invite)\b.{0,48}\b(create|update|cancel|delete)\b",
    re.I,
)
_SHORT_CONFIRM = frozenset(
    {"yes", "y", "ok", "okay", "sure", "done", "go ahead", "do it", "please do"}
)


def is_short_confirm(message: str) -> bool:
    return (message or "").strip().lower() in _SHORT_CONFIRM


@dataclass(frozen=True)
class TurnPrep:
    in_scope: bool
    intent: Intent
    task: str
    search_query: str
    prefetch_mail: bool
    prefetch_commitments: bool
    prefetch_memories: bool
    scope_block: ScopeBlock = "clarify"
    clarify_reply: str = ""

    @property
    def skip_retrieval(self) -> bool:
        return self.in_scope and self.intent == "converse" and not (
            self.prefetch_mail or self.prefetch_commitments or self.prefetch_memories
        )

    def blocks_chat_turn(self) -> bool:
        """Stop before the agent only on trivia sorry or an explicit CLARIFY line."""
        if self.in_scope:
            return False
        if self.scope_block == "sorry":
            return True
        return bool(self.clarify_reply.strip())


def format_router_history(
    history: list[dict] | None,
    *,
    max_turns: int | None = None,
) -> str:
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


def _parse_yes_no(line: str) -> bool | None:
    lower = (line or "").strip().lower()
    if lower in ("yes", "y", "true", "in"):
        return True
    if lower in ("no", "n", "false", "out"):
        return False
    return None


def _parse_prefetch(text: str) -> tuple[bool, bool, bool] | None:
    """None when PREFETCH line is absent; otherwise the three flags."""
    upper = (text or "").upper()
    if "PREFETCH:" not in upper:
        return None
    for line in (text or "").splitlines():
        if not line.strip().upper().startswith("PREFETCH:"):
            continue
        raw = line.split(":", 1)[1].strip().lower()
        if raw in ("none", "-", ""):
            return False, False, False
        parts = {p.strip() for p in re.split(r"[,;]", raw) if p.strip()}
        mail = "mail" in parts or "gmail" in parts
        commitments = "commitments" in parts or "commitment" in parts
        memories = "memories" in parts or "memory" in parts
        return mail, commitments, memories
    return None


def _parse_clarify_reply(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip().upper().startswith("CLARIFY:"):
            return line.split(":", 1)[1].strip()
    return ""


def _parse_scope_block(text: str) -> ScopeBlock:
    for line in (text or "").splitlines():
        if not line.strip().upper().startswith("BLOCK:"):
            continue
        rest = line.split(":", 1)[1].strip().lower()
        if "sorry" in rest:
            return "sorry"
        if "clarify" in rest:
            return "clarify"
    return "clarify"


def _parse_intent_line(text: str) -> Intent | None:
    for row in (text or "").splitlines():
        if not row.strip().upper().startswith("INTENT:"):
            continue
        token = row.split(":", 1)[1].strip().lower().split(None, 1)[0] if ":" in row else ""
        if token in ("converse", "plan", "execute"):
            return token  # type: ignore[return-value]
        return None
    return None


def parse_turn_prep(text: str, *, message: str, history: list[dict] | None) -> TurnPrep | None:
    """Parse router model output; None when INTENT is missing."""
    intent = _parse_intent_line(text)
    if intent is None:
        return None
    in_scope = True
    for line in (text or "").splitlines():
        if line.strip().upper().startswith("SCOPE:"):
            parsed = _parse_yes_no(line.split(":", 1)[1])
            if parsed is not None:
                in_scope = parsed
            break
    task = message.strip()
    search = task
    upper = (text or "").upper()
    if "TASK:" in upper:
        for line in (text or "").splitlines():
            if line.strip().upper().startswith("TASK:"):
                task = line.split(":", 1)[1].strip() or task
                break
    if "SEARCH:" in upper:
        for line in (text or "").splitlines():
            if line.strip().upper().startswith("SEARCH:"):
                search = line.split(":", 1)[1].strip() or task
                break
    parsed_prefetch = _parse_prefetch(text)
    if parsed_prefetch is None:
        if intent == "converse":
            mail = commitments = memories = False
        elif in_scope and intent in ("plan", "execute"):
            memories = True
            mail = bool(_WORKSPACE_HINT.search(task))
            commitments = False
        else:
            mail = commitments = memories = False
            if in_scope and _WORKSPACE_HINT.search(task):
                mail = "mail" in task.lower() or "gmail" in task.lower()
    else:
        mail, commitments, memories = parsed_prefetch
        if not mail and not commitments and not memories and in_scope:
            if intent in ("plan", "execute"):
                memories = True
                mail = bool(_WORKSPACE_HINT.search(task))
            elif intent != "converse" and _WORKSPACE_HINT.search(task):
                mail = "mail" in task.lower() or "gmail" in task.lower()
    if history and is_short_confirm(message) and thread_pending_execute(history):
        in_scope = True
    scope_block = _parse_scope_block(text) if not in_scope else "clarify"
    clarify_reply = _parse_clarify_reply(text) if not in_scope else ""
    return TurnPrep(
        in_scope=in_scope,
        intent=intent,
        task=task,
        search_query=search,
        prefetch_mail=mail,
        prefetch_commitments=commitments,
        prefetch_memories=memories,
        scope_block=scope_block,
        clarify_reply=clarify_reply,
    )


def fast_path_turn_prep(message: str) -> TurnPrep | None:
    """No LLM: slash commands and lone greetings."""
    msg = (message or "").strip()
    if not msg:
        return TurnPrep(
            in_scope=False,
            intent="converse",
            task="",
            search_query="",
            prefetch_mail=False,
            prefetch_commitments=False,
            prefetch_memories=False,
        )
    if msg.startswith("/"):
        return TurnPrep(
            in_scope=True,
            intent="execute",
            task=msg,
            search_query=msg,
            prefetch_mail=False,
            prefetch_commitments=False,
            prefetch_memories=True,
        )
    if _GREETING_START.match(msg) and len(msg) < 48:
        return TurnPrep(
            in_scope=True,
            intent="converse",
            task=msg,
            search_query=msg,
            prefetch_mail=False,
            prefetch_commitments=False,
            prefetch_memories=False,
        )
    return None


def is_vague_thread_followup(message: str, history: list[dict] | None) -> bool:
    """Short anaphoric / continuation line after an assistant reply — may need retrieval."""
    msg = (message or "").strip()
    if not history or len(msg) > 100:
        return False
    saw_assistant = any(
        (h.get("role") or "") == "assistant" for h in (history or [])[-8:]
    )
    if not saw_assistant:
        return False
    # Do not use looks_like_thread_continuation (any ≤32 chars) — that catches trivia.
    if is_short_confirm(msg):
        return True
    if _VAGUE_ANAPHORA.search(msg):
        return True
    # Pure stubs ("hmm", "ok?") — not multi-word trivia questions.
    if (
        len(msg) <= 32
        and len(msg.split()) <= 2
        and not _WORKSPACE_HINT.search(msg)
        and not _HTTP_URL.search(msg)
    ):
        return True
    return False


def has_execute_verbs(text: str) -> bool:
    return bool(_EXECUTE_VERBS.search(text or ""))


def fallback_turn_prep(message: str, history: list[dict] | None) -> TurnPrep:
    """Degraded mode or LLM failure."""
    msg = (message or "").strip()
    fast = fast_path_turn_prep(msg)
    if fast is not None:
        return fast
    in_scope = bool(_WORKSPACE_HINT.search(msg) or _HTTP_URL.search(msg))
    if history and is_short_confirm(msg) and thread_pending_execute(history):
        in_scope = True
    mail = bool(_WORKSPACE_HINT.search(msg) and re.search(r"\b(mail|gmail|inbox)\b", msg, re.I))
    commitments = bool(re.search(r"\b(commit|todo|deadline|promise)\b", msg, re.I))
    intent: Intent = "execute" if has_execute_verbs(msg) else "plan"
    return TurnPrep(
        in_scope=in_scope,
        intent=intent,
        task=msg,
        search_query=msg,
        prefetch_mail=mail,
        prefetch_commitments=commitments,
        prefetch_memories=in_scope and len(msg) >= 8,
        scope_block="clarify",
    )


async def prepare_turn(gateway, message: str, history: list[dict] | None) -> TurnPrep:
    msg = (message or "").strip()
    fast = fast_path_turn_prep(msg)
    if fast is not None:
        return fast
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return fallback_turn_prep(msg, history)
    hist = format_router_history(history, max_turns=ROUTER_HISTORY_TURNS)
    parts = [
        "You route user turns for MindOS (personal mail, calendar, files, indexed notes only).",
        "General knowledge unrelated to the user's mail/calendar/files: SCOPE: no and BLOCK: sorry.",
        "Interview/meeting dates, invites, or share links from the user's mail or calendar: SCOPE: yes.",
        "Only when truly ambiguous (one vague line): SCOPE: no, BLOCK: clarify, and CLARIFY: question.",
        "Output exactly these lines:",
        "SCOPE: yes|no",
        "BLOCK: sorry|clarify (required when SCOPE: no)",
        "CLARIFY: one short question back to the user (required when BLOCK: clarify; mail/calendar/files)",
        "INTENT: converse|plan|execute",
        "TASK: full task for this turn (fix typos; merge prior turns when needed)",
        "SEARCH: one line for retrieval search (keywords from the task)",
        "PREFETCH: comma list from mail, commitments, memories or none",
        "",
        "converse = Q&A over indexed data, no multi-step tool work.",
        "plan = search/list/analyze indexed data.",
        "execute = calendar, files, integrations — changes outside chat.",
        "",
        f"Latest user message:\n{clip_text(msg, ROUTER_LLM_MAX_CHARS)}",
    ]
    if hist:
        parts.insert(-1, f"Recent conversation:\n{hist}\n")
    prompt = "\n".join(parts)
    try:
        res = await gateway.complete(
            prompt,
            task=LlmTask.ROUTER,
            max_output_tokens=ROUTER_MAX_OUTPUT_TOKENS,
        )
        parsed = parse_turn_prep(res.text or "", message=msg, history=history)
        if parsed is not None:
            return parsed
    except Exception:
        logger.warning("router_turn_prep_failed", exc_info=True)
    return fallback_turn_prep(msg, history)
