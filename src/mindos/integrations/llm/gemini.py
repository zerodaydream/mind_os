"""Gemini adapter for the `LLMProvider` port (ARCHITECTURE section 3.5).

One domain `ReasoningEffort` maps to `thinking_level` on Gemini 3 and later, or to
`thinking_budget` on Gemini 2.5. Supported levels come from the model profile that ships with
`langchain-google-genai`, so new models need no code change. Thought text is returned
separately from the answer and is treated as untrusted by callers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from opsagent.domain import (
    GEMINI_API_KEY,
    CredentialProvider,
    LLMRequest,
    LLMResult,
    ModelCapabilities,
    ModelRole,
    ReasoningEffort,
    Usage,
    resolve_effort,
)

ChatFactory = Callable[..., BaseChatModel]
MAX_RETRIES = 2
REQUEST_TIMEOUT_S = 90


class MissingCredentialError(RuntimeError):
    pass


class GeminiProvider:
    def __init__(
        self,
        *,
        models: Mapping[ModelRole, str],
        credentials: CredentialProvider,
        include_thoughts: bool = True,
        thinking_budget: int | None = None,
        chat_factory: ChatFactory | None = None,
    ) -> None:
        self._models = dict(models)
        self._credentials = credentials
        self._include_thoughts = include_thoughts
        self._thinking_budget = thinking_budget
        self._factory: ChatFactory = chat_factory or ChatGoogleGenerativeAI
        self._capabilities: dict[str, ModelCapabilities] = {}

    # ---- port ------------------------------------------------------------------------------
    def capabilities(self, role: ModelRole) -> ModelCapabilities:
        model_id = self._model_id(role)
        if model_id not in self._capabilities:
            profile = getattr(self._build(model_id), "profile", None) or {}
            levels = tuple(
                ReasoningEffort(v)
                for v in profile.get("reasoning_effort_levels", ())
                if v in ReasoningEffort._value2member_map_
            )
            default = str(profile.get("reasoning_effort_default", ""))
            self._capabilities[model_id] = ModelCapabilities(
                model_id=model_id,
                effort_levels=levels,
                default_effort=ReasoningEffort(default)
                if default in ReasoningEffort._value2member_map_
                else None,
            )
        return self._capabilities[model_id]

    def model_id(self, role: ModelRole) -> str:
        return self._model_id(role)

    def chat(self, role: ModelRole, effort: ReasoningEffort | None) -> BaseChatModel:
        """A tool-calling chat model for the agent runtime, with effort mapped for this model.

        Outside the `LLMProvider` port on purpose: only the agent adapter needs framework types.
        """
        caps = self.capabilities(role)
        resolved = resolve_effort(effort, caps.effort_levels) if effort is not None else None
        return self._build(caps.model_id, effort=resolved.effort if resolved else None)

    async def complete(self, request: LLMRequest) -> LLMResult:
        caps = self.capabilities(request.role)
        resolved = (
            resolve_effort(request.effort, caps.effort_levels)
            if request.effort is not None
            else None
        )
        effort = resolved.effort if resolved else None
        chat = self._build(caps.model_id, effort=effort)

        messages: list[BaseMessage] = []
        if request.system:
            messages.append(SystemMessage(request.system))
        messages.append(HumanMessage(request.prompt))
        reply = await chat.ainvoke(messages)

        text, thinking = _split_content(reply)
        return LLMResult(
            text=text,
            thinking=thinking,
            usage=_usage(reply),
            model_id=caps.model_id,
            effort_applied=effort,
        )

    # ---- internals -------------------------------------------------------------------------
    def _model_id(self, role: ModelRole) -> str:
        try:
            return self._models[role]
        except KeyError:
            raise KeyError(f"No model configured for role '{role.value}'") from None

    def _build(self, model_id: str, *, effort: ReasoningEffort | None = None) -> BaseChatModel:
        key = self._credentials.get(GEMINI_API_KEY)
        if not key:
            raise MissingCredentialError("Gemini API key is not configured.")
        kwargs: dict[str, Any] = {
            "model": model_id,
            "google_api_key": key,
            "include_thoughts": self._include_thoughts,
            "max_retries": MAX_RETRIES,  # section 11.2: at most 3 attempts per call
            "timeout": REQUEST_TIMEOUT_S,
        }
        if effort is not None:
            kwargs["thinking_level"] = effort.value
        elif self._thinking_budget is not None:
            kwargs["thinking_budget"] = self._thinking_budget
        return self._factory(**kwargs)


def _split_content(reply: AIMessage) -> tuple[str, str | None]:
    """Separate answer text from thought-summary text in a Gemini reply."""
    content = reply.content
    if isinstance(content, str):
        return content, None
    answer: list[str] = []
    thoughts: list[str] = []
    for block in content:
        if isinstance(block, str):
            answer.append(block)
        elif block.get("type") == "thinking":
            thoughts.append(str(block.get("thinking", "")))
        elif block.get("type") == "text":
            answer.append(str(block.get("text", "")))
    return "".join(answer), ("".join(thoughts) or None)


def _usage(reply: AIMessage) -> Usage:
    meta: Mapping[str, Any] = reply.usage_metadata or {}
    input_details = meta.get("input_token_details") or {}
    output_details = meta.get("output_token_details") or {}
    return Usage(
        input_tokens=int(meta.get("input_tokens", 0)),
        output_tokens=int(meta.get("output_tokens", 0)),
        cached_tokens=int(input_details.get("cache_read", 0)),
        reasoning_tokens=int(output_details.get("reasoning", 0)),
    )
