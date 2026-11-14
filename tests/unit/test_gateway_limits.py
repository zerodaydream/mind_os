import pytest

from backend.common.utils.enums import LlmMode
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.gateway import build_llm_gateway


@pytest.mark.asyncio
async def test_prompt_is_clamped() -> None:
    gw = build_llm_gateway(Settings(mindos_llm_mode=LlmMode.MOCK))
    huge = "x" * 100_000
    out = await gw.complete(huge)
    assert len(out.text) < len(huge)
