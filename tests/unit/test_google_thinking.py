from types import SimpleNamespace

from backend.common.utils.enums import LlmMode, LlmTask
from backend.common.config_loader.settings import Settings
from backend.services.llm.core.provider_gateway import build_provider_gateway
from backend.services.llm.providers.google import (
    _parts_from_chunk,
    build_gemini_thinking_config,
    gemini_model_major,
    google_generate_config,
)


def test_gemini_model_major() -> None:
    assert gemini_model_major("gemini-3.5-flash-lite") == 3
    assert gemini_model_major("gemini-2.5-flash") == 2


def test_build_thinking_config_gemini_3() -> None:
    cfg = build_gemini_thinking_config("gemini-3.5-flash-lite", "LOW")
    assert cfg == {"include_thoughts": True, "thinking_level": "LOW"}


def test_build_thinking_config_off() -> None:
    assert build_gemini_thinking_config("gemini-3.5-flash-lite", "OFF") is None


def test_gateway_thinking_disabled_for_chat_tasks() -> None:
    gw = build_provider_gateway(
        Settings(mindos_llm_mode=LlmMode.MOCK, mindos_thinking_level="MEDIUM")
    )
    m = "gemini-3.5-flash-lite"
    assert gw.thinking_config_for(LlmTask.SYNTH, m) is None
    assert gw.thinking_config_for(LlmTask.ROUTER, m) is None
    assert gw.thinking_config_for(LlmTask.EXTRACT, m) is None


def test_parts_from_chunk_thought_flag() -> None:
    part = SimpleNamespace(text="inner monologue", thought=True)
    content = SimpleNamespace(parts=[part])
    cand = SimpleNamespace(content=content)
    chunk = SimpleNamespace(candidates=[cand], text="")
    assert list(_parts_from_chunk(chunk)) == [("think", "inner monologue")]