"""Calls the real Gemini API. Opt in with OPS_TEST_LIVE=1 and a key in .env or the environment."""

from __future__ import annotations

import os

import pytest

from opsagent.bootstrap import build_container
from opsagent.domain import LLMRequest, ModelRole, ReasoningEffort
from opsagent.infra import load_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("OPS_TEST_LIVE") != "1", reason="set OPS_TEST_LIVE=1"),
]


async def test_reasoning_round_trip() -> None:
    settings = load_settings()
    container = build_container(settings)
    caps = container.llm.capabilities(ModelRole.ORCHESTRATOR)
    result = await container.llm.complete(
        LLMRequest(
            role=ModelRole.ORCHESTRATOR,
            prompt="What is 17 * 23? Reply with the number only.",
            effort=ReasoningEffort.LOW,
        )
    )
    assert "391" in result.text
    assert result.model_id == caps.model_id
    assert result.usage.output_tokens > 0
