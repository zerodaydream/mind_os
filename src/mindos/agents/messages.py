"""Helpers over LangChain message content. Answer text and thought text are always separated."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.messages import AIMessage

from opsagent.domain import Usage


def split_content(content: Any) -> tuple[str, str]:
    """Return (answer text, thought-summary text). Thought text is untrusted display data."""
    if isinstance(content, str):
        return content, ""
    answer: list[str] = []
    thoughts: list[str] = []
    for block in content or ():
        if isinstance(block, str):
            answer.append(block)
        elif isinstance(block, Mapping):
            kind = block.get("type")
            if kind == "thinking":
                thoughts.append(str(block.get("thinking", "")))
            elif kind == "text":
                answer.append(str(block.get("text", "")))
    return "".join(answer), "".join(thoughts)


def usage_of(message: AIMessage) -> Usage:
    meta: Mapping[str, Any] = message.usage_metadata or {}
    input_details = meta.get("input_token_details") or {}
    output_details = meta.get("output_token_details") or {}
    return Usage(
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
        cached_tokens=int(input_details.get("cache_read", 0)),
        reasoning_tokens=int(output_details.get("reasoning", 0)),
    )
