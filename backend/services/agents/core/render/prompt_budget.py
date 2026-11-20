"""Shared prompt size limits and sentence-safe clipping."""

from __future__ import annotations

# Retrieval prefetch
PREFETCH_SEARCH_LIMIT = 8
PREFETCH_GMAIL_ROWS = 20
PREFETCH_CTX_ARTIFACTS = 6
PREFETCH_COMMITMENTS = 6
PREFETCH_MEMORIES = 6
PREFETCH_MAIL_PREVIEW = 360
PREFETCH_GMAIL_BODY = 2_400
PREFETCH_CONTEXT_MAX = 7_000
DEEP_USER_CONTEXT_MAX = 6_000

# Conversation + synth
CHAT_HISTORY_TURNS = 6
CHAT_HISTORY_MSG_CHARS = 1_600
# Deep agent: LangGraph checkpoint holds the thread; short recap in the user message.
DEEP_CHAT_HISTORY_TURNS = 4
# Router LLM: fewer turns than synth — TASK merges prior context.
ROUTER_HISTORY_TURNS = 4
ROUTER_LLM_MAX_CHARS = 1_200
# Keep answer headroom above mindos_thinking_budget (default 1024).
SYNTH_MAX_OUTPUT_TOKENS = 3_072
EXTRACT_MAX_OUTPUT_TOKENS = 32
# Router TASK lines can merge several turns; 32 tokens truncates merged tasks.
ROUTER_MAX_OUTPUT_TOKENS = 256


def clip_text(text: str, max_chars: int, *, min_keep: int | None = None) -> str:
    """Shorten without chopping mid-sentence when possible."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    floor = min_keep if min_keep is not None else min(48, max(12, max_chars // 3))
    chunk = text[:max_chars]
    for sep in (". ", ".\n", "\n", "; ", ", ", " "):
        idx = chunk.rfind(sep)
        if idx >= floor:
            end = idx + (1 if sep != " " else 0)
            return chunk[:end].rstrip() + "…"
    return chunk.rstrip() + "…"
