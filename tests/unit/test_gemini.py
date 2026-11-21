from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from opsagent.domain import GEMINI_API_KEY, LLMRequest, ModelRole, ReasoningEffort
from opsagent.integrations.llm import GeminiProvider, MissingCredentialError


class Creds:
    def __init__(self, value: str | None = "k") -> None:
        self.value = value

    def get(self, name: str) -> str | None:
        return self.value if name == GEMINI_API_KEY else None


class FakeGemini(GenericFakeChatModel):
    """Records how the adapter configured it, and carries a model profile."""

    profile: dict[str, Any] | None = None
    kwargs_seen: dict[str, Any] = {}


def make_factory(levels: list[str], reply: AIMessage, seen: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    def factory(**kwargs: Any) -> FakeGemini:
        seen.append(kwargs)
        return FakeGemini(
            messages=iter([reply]),
            profile={"reasoning_effort_levels": levels, "reasoning_effort_default": "high"}
            if levels
            else {},
        )

    return factory


def provider(factory: Any, creds: Creds | None = None, **kw: Any) -> GeminiProvider:
    return GeminiProvider(
        models={ModelRole.ORCHESTRATOR: "gemini-3-x", ModelRole.SUBAGENT: "gemini-2.5-x"},
        credentials=creds or Creds(),
        chat_factory=factory,
        **kw,
    )


def test_capabilities_come_from_profile() -> None:
    seen: list[dict[str, Any]] = []
    p = provider(make_factory(["minimal", "low", "high"], AIMessage("x"), seen))
    caps = p.capabilities(ModelRole.ORCHESTRATOR)
    assert caps.effort_levels == (
        ReasoningEffort.MINIMAL,
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
    )
    assert caps.default_effort == ReasoningEffort.HIGH
    p.capabilities(ModelRole.ORCHESTRATOR)
    assert len(seen) == 1  # cached


async def test_effort_maps_to_thinking_level_and_falls_back() -> None:
    seen: list[dict[str, Any]] = []
    p = provider(make_factory(["minimal", "low", "high"], AIMessage("answer"), seen))
    result = await p.complete(
        LLMRequest(role=ModelRole.ORCHESTRATOR, prompt="hi", effort=ReasoningEffort.MEDIUM)
    )
    assert seen[-1]["thinking_level"] == "low"  # medium unsupported: nearest lower
    assert result.effort_applied == ReasoningEffort.LOW
    assert result.text == "answer"


async def test_thinking_blocks_and_usage_are_separated() -> None:
    reply = AIMessage(
        content=[
            {"type": "thinking", "thinking": "step one"},
            {"type": "text", "text": "final"},
        ],
        usage_metadata={
            "input_tokens": 10,
            "output_tokens": 30,
            "total_tokens": 40,
            "input_token_details": {"cache_read": 4},
            "output_token_details": {"reasoning": 20},
        },
    )
    p = provider(make_factory(["low"], reply, []))
    r = await p.complete(LLMRequest(role=ModelRole.ORCHESTRATOR, prompt="q"))
    assert r.text == "final" and r.thinking == "step one"
    assert (r.usage.input_tokens, r.usage.cached_tokens, r.usage.reasoning_tokens) == (10, 4, 20)


async def test_no_effort_support_uses_thinking_budget() -> None:
    seen: list[dict[str, Any]] = []
    p = provider(make_factory([], AIMessage("x"), seen), thinking_budget=2048)
    await p.complete(LLMRequest(role=ModelRole.SUBAGENT, prompt="q", effort=ReasoningEffort.HIGH))
    assert seen[-1]["thinking_budget"] == 2048
    assert "thinking_level" not in seen[-1]


async def test_missing_key_and_missing_role() -> None:
    p = provider(make_factory([], AIMessage("x"), []), creds=Creds(None))
    with pytest.raises(MissingCredentialError):
        await p.complete(LLMRequest(role=ModelRole.SUBAGENT, prompt="q"))
    with pytest.raises(KeyError, match="utility"):
        provider(make_factory([], AIMessage("x"), [])).capabilities(ModelRole.UTILITY)


async def test_api_key_never_logged_in_result() -> None:
    p = provider(make_factory(["low"], AIMessage("x"), []), creds=Creds("SECRETKEY"))
    r = await p.complete(LLMRequest(role=ModelRole.ORCHESTRATOR, prompt="q"))
    assert "SECRETKEY" not in r.model_dump_json()
