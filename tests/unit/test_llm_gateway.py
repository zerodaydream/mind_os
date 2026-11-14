import pytest

from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.utils.exceptions import LlmDisabledError
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.gateway import build_llm_gateway


@pytest.mark.asyncio
async def test_mock_complete(mock_gateway) -> None:
    result = await mock_gateway.complete("hello", task=LlmTask.SYNTH)
    assert result.provider == "mock"
    assert "hello" in result.text
    assert result.model == mock_gateway.model_for(LlmTask.SYNTH)


@pytest.mark.asyncio
async def test_degraded_blocks() -> None:
    gw = build_llm_gateway(Settings(mindos_llm_mode=LlmMode.DEGRADED))
    with pytest.raises(LlmDisabledError):
        await gw.complete("x")


def test_model_for_extract_vs_synth() -> None:
    gw = build_llm_gateway(
        Settings(
            mindos_llm_mode=LlmMode.MOCK,
            mindos_extract_model="gemini-2.5-flash",
            mindos_synth_model="gemini-3.5-flash-lite",
        )
    )
    assert gw.model_for(LlmTask.EXTRACT) == "gemini-2.0-flash"
    assert gw.model_for(LlmTask.SYNTH) == "gemini-2.5-pro"
