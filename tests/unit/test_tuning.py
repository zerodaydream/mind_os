"""Changing the model and reasoning effort of the orchestrator and subagents at run time."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage

import mindos.integrations.llm.gemini as gemini_module
from mindos.agents import ModelTuner
from mindos.domain import ModelRole, ReasoningEffort
from mindos.integrations.llm import GeminiProvider
from tests.support.agent_kit import make_kit
from tests.support.scripted_model import ScriptedChatModel, reply
from tests.unit.test_agent_runtime import delegate
from tests.unit.test_gemini import Creds, make_factory

ORCH, SUB = ModelRole.ORCHESTRATOR, ModelRole.SUBAGENT
HIGH, LOW, MIN = ReasoningEffort.HIGH, ReasoningEffort.LOW, ReasoningEffort.MINIMAL


class Catalog:
    """A provider catalog: which models exist and which reasoning levels each supports."""

    def __init__(self) -> None:
        self.models = {ORCH: "m-big", SUB: "m-small"}
        self.levels = {
            "m-big": (MIN, LOW, HIGH),
            "m-small": (LOW, HIGH),
            "m-plain": (),
        }
        self.listed = ("m-big", "m-small", "m-plain", "m-new")

    async def list_models(self) -> tuple[str, ...]:
        return self.listed

    def effort_levels(self, model_id: str) -> tuple[ReasoningEffort, ...]:
        return self.levels[model_id]

    def select_model(self, role: ModelRole, model_id: str) -> None:
        self.models[role] = model_id


def tuner(catalog: Catalog | None = None, **kw: Any) -> tuple[ModelTuner, Catalog]:
    catalog = catalog or Catalog()
    t = ModelTuner(
        efforts={ORCH: HIGH, SUB: LOW}, model_id=lambda r: catalog.models[r], catalog=catalog, **kw
    )
    return t, catalog


async def test_the_list_merges_configured_extra_and_provider_models_without_repeats() -> None:
    t, _ = tuner(extra_models=("m-extra", "m-big"))
    assert await t.available_models() == ("m-big", "m-small", "m-extra", "m-plain", "m-new")


def test_effort_can_be_set_per_role_and_is_checked_against_the_model() -> None:
    t, _ = tuner()
    assert t.select(SUB, effort=HIGH).effort is HIGH and t.effort(SUB) is HIGH
    assert t.effort(ORCH) is HIGH  # the other role is untouched
    with pytest.raises(ValueError, match="m-small supports: low, high"):
        t.select(SUB, effort=MIN)
    assert t.effort(SUB) is HIGH  # a refused change changes nothing


def test_switching_model_adjusts_an_effort_the_new_model_lacks() -> None:
    t, c = tuner()
    t.select(ORCH, effort=MIN)
    choice = t.select(SUB, model_id="m-big")  # subagent was LOW: still valid
    assert (choice.model_id, choice.effort) == ("m-big", LOW) and c.models[SUB] == "m-big"
    t.select(ORCH, effort=MIN)
    shrunk = t.select(ORCH, model_id="m-small")  # m-small has no MINIMAL: nearest supported
    assert shrunk.effort is LOW


def test_a_model_with_no_configurable_reasoning_refuses_an_effort() -> None:
    t, _ = tuner()
    choice = t.select(SUB, model_id="m-plain")
    assert choice.model_id == "m-plain"
    with pytest.raises(ValueError, match="no configurable reasoning"):
        t.select(SUB, effort=LOW)


async def test_an_unknown_model_is_refused_once_the_list_is_known() -> None:
    t, c = tuner()
    await t.available_models()
    with pytest.raises(ValueError, match="not one of the available"):
        t.select(ORCH, model_id="made-up")
    assert c.models[ORCH] == "m-big"


def test_without_a_catalog_only_effort_changes() -> None:
    t = ModelTuner(efforts={ORCH: HIGH}, model_id=lambda r: "fixed")
    assert t.select(ORCH, effort=LOW).effort is LOW
    with pytest.raises(ValueError, match="cannot be changed"):
        t.select(ORCH, model_id="other")


# ---- the runtime uses the choice from the next call -------------------------------------------
async def test_a_changed_effort_and_model_apply_to_the_next_run_for_both_roles() -> None:
    orchestrator = ScriptedChatModel(script=[reply(calls=[delegate()]), reply("one"), reply("two")])
    subagent = ScriptedChatModel(script=[reply("sub one"), reply("sub two")])
    kit = make_kit(orchestrator, subagent)
    await kit.collect(kit.spec("first"))
    assert (ORCH, HIGH) in kit.models.requested and (SUB, LOW) in kit.models.requested

    kit.runtime.tuner.select(ORCH, effort=LOW)
    kit.runtime.tuner.select(SUB, effort=HIGH)
    kit.models.requested.clear()
    orchestrator.load(reply(calls=[delegate(call_id="d2")]), reply("two"))
    subagent.load(reply("sub two"))
    await kit.collect(kit.spec("second"))
    assert (ORCH, LOW) in kit.models.requested  # a new orchestrator graph for the new effort
    assert (SUB, HIGH) in kit.models.requested  # and a new subagent graph


# ---- the Gemini catalog -----------------------------------------------------------------------
def gemini(seen: list[dict[str, Any]]) -> GeminiProvider:
    return GeminiProvider(
        models={ORCH: "g-a", SUB: "g-b"},
        credentials=Creds(),
        chat_factory=make_factory(["low", "high"], AIMessage("x"), seen),
    )


def test_the_provider_switches_model_per_role_and_reads_levels_per_model() -> None:
    seen: list[dict[str, Any]] = []
    p = gemini(seen)
    assert p.model_id(ORCH) == "g-a"
    p.select_model(ORCH, "g-c")
    assert p.model_id(ORCH) == "g-c" and p.model_id(SUB) == "g-b"
    assert p.effort_levels("g-c") == (LOW, HIGH)
    p.chat(ORCH, HIGH)
    assert seen[-1]["model"] == "g-c"


class _Model:
    def __init__(self, name: str, actions: list[str]) -> None:
        self.name, self.supported_actions = name, actions


class _Pager:
    def __init__(self, items: list[_Model]) -> None:
        self._items = items

    def __aiter__(self) -> _Pager:
        self._iter = iter(self._items)
        return self

    async def __anext__(self) -> _Model:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None


class _Models:
    def __init__(self, items: list[_Model]) -> None:
        self._items = items

    async def list(self) -> _Pager:
        return _Pager(self._items)


def fake_client(items: list[_Model]) -> Any:
    class Client:
        def __init__(self, **_: Any) -> None:
            self.aio = type("Aio", (), {"models": _Models(items)})()

    return Client


async def test_the_live_list_keeps_only_gemini_chat_models(monkeypatch: pytest.MonkeyPatch) -> None:
    items = [
        _Model("models/gemini-x-pro", ["generateContent"]),
        _Model("models/gemini-embedding", ["embedContent"]),
        _Model("models/imagen-y", ["generateContent"]),
        _Model("models/gemini-x-flash", ["generateContent", "countTokens"]),
    ]
    monkeypatch.setattr(gemini_module.genai, "Client", fake_client(items))
    assert await gemini([]).list_models() == ("gemini-x-pro", "gemini-x-flash")


async def test_a_failing_or_keyless_list_is_empty_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("network down")

    monkeypatch.setattr(gemini_module.genai, "Client", Broken)
    assert await gemini([]).list_models() == ()
    keyless = GeminiProvider(models={ORCH: "g"}, credentials=Creds(None))
    assert await keyless.list_models() == ()
