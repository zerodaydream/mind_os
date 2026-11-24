from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai import _function_utils as fu
from pydantic import BaseModel, Field

from mindos.agents.bridge import stub_tools
from mindos.domain import (
    ModelRole,
    Pricing,
    ReasoningEffort,
    SubagentResult,
    Usage,
    estimate_cost,
)
from mindos.infra import Settings, resolve_timezone
from mindos.integrations.calendar import FixtureCalendar, sample_events
from mindos.integrations.llm import GeminiProvider
from mindos.tools import model_schema
from mindos.tools.core import WriteTodosInput
from tests.support.agent_kit import make_kit
from tests.support.scripted_model import ScriptedChatModel

SRC = Path(__file__).resolve().parents[2] / "src" / "mindos"
IST = ZoneInfo("Asia/Kolkata")


def test_only_the_engine_calls_tool_handlers() -> None:
    """ARCHITECTURE 4.1: `execute` is the only path to a handler, hence to an adapter."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "engine.py" and path.parent.name == "tools":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "handler"
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []


def test_success_that_claims_side_effects_needs_evidence() -> None:
    with pytest.raises(ValueError, match="evidence"):
        SubagentResult(status="success", summary="sent", side_effects=("sent mail",))
    assert (
        SubagentResult(status="partial", summary="maybe", side_effects=("sent",)).status
        == "partial"
    )


def test_cost_estimate() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=500_000)
    assert estimate_cost(usage, Pricing(1.0, 4.0)) == pytest.approx(3.0)
    assert estimate_cost(usage, None) is None


def test_settings_pricing_needs_both_prices() -> None:
    assert Settings(price_input_per_mtok=1.0).pricing() is None
    assert Settings(price_input_per_mtok=1.0, price_output_per_mtok=2.0).pricing() == Pricing(
        1.0, 2.0
    )


def test_timezone_resolution(tmp_path: Path) -> None:
    assert resolve_timezone("Asia/Kolkata") == IST
    with pytest.raises(ValueError, match="Unknown time zone"):
        resolve_timezone("Mars/Base")
    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/Asia/Tokyo")
    if Path("/usr/share/zoneinfo/Asia/Tokyo").exists():
        assert str(resolve_timezone(None, localtime=link)) == "Asia/Tokyo"
    assert str(resolve_timezone(None, localtime=tmp_path / "missing")) == "UTC"


async def test_fixture_calendar_filters_by_overlap_query_and_limit() -> None:
    now = datetime(2026, 11, 22, 6, 0, tzinfo=UTC)
    cal = FixtureCalendar(sample_events(now, IST))
    start = datetime(2026, 11, 22, tzinfo=IST)
    week = await cal.search_events(start=start, end=start + timedelta(days=7), query=None, limit=50)
    assert len(week) == 6 and week == sorted(week, key=lambda e: e.start)
    standups = await cal.search_events(
        start=start, end=start + timedelta(days=7), query="STANDUP", limit=50
    )
    assert {e.title for e in standups} == {"Team standup"} and len(standups) == 2
    assert (
        len(
            await cal.search_events(start=start, end=start + timedelta(days=7), query=None, limit=2)
        )
        == 2
    )
    assert (await cal.get_event("evt_design")) is not None and (await cal.get_event("zzz")) is None


def test_model_schema_inlines_refs_and_keeps_property_names() -> None:
    class Inner(BaseModel):
        title: str

    class Outer(BaseModel):
        items: list[Inner] = Field(max_length=3)

    schema = model_schema(Outer)
    assert "$defs" not in str(schema) and "$ref" not in str(schema)
    assert schema["properties"]["items"]["items"]["properties"] == {"title": {"type": "string"}}
    todos = model_schema(WriteTodosInput)
    assert todos["properties"]["todos"]["items"]["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "done",
    ]


def test_our_tool_schemas_convert_to_gemini_declarations() -> None:
    """Guards against schema features the Gemini adapter cannot express."""
    kit = make_kit(ScriptedChatModel(script=[]))
    engine = kit.runtime._engine
    for agent in ("orchestrator", "calendar"):
        tools = stub_tools(engine, agent)
        declared = fu.convert_to_genai_function_declarations(tools)
        names = {fd.name for t in declared for fd in t.function_declarations}
        assert names == {t.name for t in tools}
        ChatGoogleGenerativeAI(model="gemini-2.5-flash", google_api_key="k" * 20).bind_tools(tools)


def test_chat_model_maps_effort_per_model_profile() -> None:
    seen: list[dict[str, object]] = []

    class Profiled(ScriptedChatModel):
        profile: dict[str, object] | None = None

    def factory(**kwargs: object) -> Profiled:
        seen.append(kwargs)
        return Profiled(
            script=[],
            profile={"reasoning_effort_levels": ["minimal", "low", "high"]},
        )

    class Creds:
        def get(self, name: str) -> str | None:
            return "k"

    provider = GeminiProvider(
        models={ModelRole.ORCHESTRATOR: "gemini-3-x"}, credentials=Creds(), chat_factory=factory
    )
    assert provider.model_id(ModelRole.ORCHESTRATOR) == "gemini-3-x"
    provider.chat(ModelRole.ORCHESTRATOR, ReasoningEffort.MEDIUM)  # unsupported: falls back to low
    provider.chat(ModelRole.ORCHESTRATOR, None)
    levels = [k.get("thinking_level") for k in seen if "thinking_level" in k]
    assert levels == ["low"]
    assert seen[-1]["max_retries"] == 2 and seen[-1]["timeout"] == 90
