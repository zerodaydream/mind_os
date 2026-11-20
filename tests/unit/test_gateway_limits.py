import pytest

from backend.common.utils.enums import LlmMode
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.provider_gateway import (
    _MAX_INPUT_CHARS,
    _clamp_prompt_keep_tail,
    build_provider_gateway,
)


@pytest.mark.asyncio
async def test_prompt_is_clamped() -> None:
    gw = build_provider_gateway(Settings(mindos_llm_mode=LlmMode.MOCK))
    huge = "x" * 100_000
    out = await gw.complete(huge)
    assert len(out.text) < len(huge)


def test_clamp_keeps_tail() -> None:
    tail = "USER_QUESTION_MARKER"
    prompt = ("HEAD" * 10_000) + tail
    clipped = _clamp_prompt_keep_tail(prompt, _MAX_INPUT_CHARS)
    assert clipped.endswith(tail)
    assert len(clipped) <= _MAX_INPUT_CHARS


@pytest.mark.asyncio
async def test_budget_records_spend() -> None:
    gw = build_provider_gateway(Settings(mindos_llm_mode=LlmMode.MOCK))
    before = gw.budget.spent_today_usd
    await gw.complete("hello world " * 20)
    assert gw.budget.spent_today_usd > before
