"""LangSmith tracing: off means off, secrets are masked, and every span is in one traced thread."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from langsmith import Client
from langsmith.utils import get_env_var, tracing_is_enabled

from mindos.bootstrap.container import build_tracing
from mindos.domain import events
from mindos.domain.commands import Decide
from mindos.infra import Settings
from mindos.integrations.tracing import LangSmithTracing, TracingConfig
from tests.support.agent_kit import make_kit
from tests.support.scripted_model import ScriptedChatModel, reply, tool_call
from tests.support.world import World
from tests.unit.test_agent_runtime import SEARCH, delegate

SECRET = "AQ.Ab8RN6" + "x" * 30  # shaped like a Google key, so `redact` masks it


class Recorder(Client):
    """A client that keeps runs in memory instead of sending them."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(api_key="test-key", auto_batch_tracing=False, **kw)
        self.posted: list[dict[str, Any]] = []

    def create_run(self, name: str, inputs: dict[str, Any], run_type: str, **kw: Any) -> None:
        self.posted.append({"name": name, "inputs": inputs, "run_type": run_type, **kw})

    def update_run(self, *args: Any, **kw: Any) -> None:
        return None

    def flush(self) -> None:
        return None


def on(client: Recorder | None = None, **kw: Any) -> LangSmithTracing:
    return LangSmithTracing(TracingConfig(enabled=True, api_key="k", **kw), client=client)


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, **kw)  # type: ignore[call-arg]


# ---- off means off ---------------------------------------------------------------------------
def test_disabled_tracing_beats_the_shell_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    get_env_var.cache_clear()
    try:
        off = LangSmithTracing(TracingConfig(enabled=False))
        with off.run(run_id="run_1", thread_id="thr_1", conversation_id=None):
            assert tracing_is_enabled() is False
    finally:
        get_env_var.cache_clear()


def test_enabled_without_a_key_stays_off_and_says_so(caplog: pytest.LogCaptureFixture) -> None:
    tracer = LangSmithTracing(TracingConfig(enabled=True, api_key=None))
    assert tracer.active is False
    with tracer.run(run_id="r", thread_id="t", conversation_id=None):
        assert tracing_is_enabled() is False
    assert "no API key" in caplog.text


def test_the_environment_never_enables_tracing_through_our_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_" + "a" * 40)
    cfg = build_tracing(settings(), _NoCredentials())
    assert cfg.active is False  # our flag is MINDOS_LANGSMITH_TRACING, and it is unset


def test_settings_turn_it_on_and_the_keychain_key_wins() -> None:
    class Keychain:
        def get(self, name: str) -> str | None:
            return "from-keychain"

    tracer = build_tracing(
        settings(langsmith_tracing=True, langsmith_api_key="from-env"), Keychain()
    )
    assert tracer.active


class _NoCredentials:
    def get(self, name: str) -> str | None:
        return None


def test_a_bad_agent_id_is_refused_up_front() -> None:
    with pytest.raises(ValueError, match="agent ID"):
        LangSmithTracing(TracingConfig(agent_id="Bad_ID"))


# ---- what leaves the process -----------------------------------------------------------------
def test_secrets_are_masked_in_traced_inputs_and_outputs() -> None:
    tracer = LangSmithTracing(TracingConfig(enabled=True, api_key="k"))
    client = tracer._client  # the real client, with our hooks installed
    assert client is not None
    payload = {"messages": [f"key is {SECRET}"], "api_key": "plain"}
    for hook in (client._hide_inputs, client._hide_outputs):
        sent = hook(payload)
        assert SECRET not in str(sent) and "plain" not in str(sent)


def test_hide_io_sends_structure_only() -> None:
    client = LangSmithTracing(TracingConfig(enabled=True, api_key="k", hide_io=True))._client
    assert client is not None and client._hide_inputs({"q": "private"}) == {}


# ---- the run tree ----------------------------------------------------------------------------
async def test_a_traced_run_lands_in_the_project_with_its_thread() -> None:
    recorder = Recorder()
    with on(recorder, project="mindos-test").run(
        run_id="run_1", thread_id="thr_1", conversation_id="c1"
    ):
        await RunnableLambda(lambda x: x).ainvoke("hi")
    assert recorder.posted
    run = recorder.posted[0]
    assert run["session_name"] == "mindos-test"
    assert run["extra"]["metadata"]["thread_id"] == "thr_1"
    assert run["extra"]["metadata"]["conversation_id"] == "c1"


async def test_the_agent_address_replaces_the_project_when_an_agent_id_is_set() -> None:
    recorder = Recorder()
    tracer = on(recorder, agent_id="mindos-agent", environment="development", project="ignored")
    with tracer.run(run_id="r", thread_id="t", conversation_id=None):
        await RunnableLambda(lambda x: x).ainvoke("hi")
    run = recorder.posted[0]
    assert run["address"] == {"kind": "AGENT", "id": "mindos-agent", "environment": "DEVELOPMENT"}
    assert run.get("session_name") != "ignored"  # the address replaces the project


async def test_orchestrator_and_subagent_spans_share_one_trace_and_thread() -> None:
    recorder = Recorder()
    orchestrator = ScriptedChatModel(script=[reply(calls=[delegate()]), reply("done")])
    subagent = ScriptedChatModel(script=[reply(calls=[SEARCH]), reply("Standup at 09:30.")])
    kit = make_kit(orchestrator, subagent, tracing=on(recorder))
    await kit.collect(kit.spec("what is on?", thread="thr_9"))
    names = [r["name"] for r in recorder.posted]
    assert "mindos.orchestrator" in names and "mindos.subagent.calendar" in names
    assert len({r["trace_id"] for r in recorder.posted}) == 1  # one run, one trace
    sub = next(r for r in recorder.posted if r["name"] == "mindos.subagent.calendar")
    assert sub["extra"]["metadata"]["thread_id"] == "thr_9"  # not inherited: set explicitly
    assert SECRET not in str([r["inputs"] for r in recorder.posted])


async def test_a_resumed_run_stays_in_the_same_thread() -> None:
    world = World()
    recorder = Recorder()
    kit = world.kit(
        reply("go", calls=[tool_call("write_todos", {"todos": []}, "t1")]),
        reply("ok"),
        tracing=on(recorder),
    )
    evts = await kit.collect(kit.spec("do it", thread="thr_1"))
    aid = next(e.approval.approval_id for e in evts if isinstance(e, events.ApprovalRequested))
    before = len(recorder.posted)
    await world.broker().resolve(aid, decision="approve")
    _ = [e async for e in kit.runtime.resume("thr_1", Decide(approval_id=aid, decision="approve"))]
    resumed = recorder.posted[before:]
    assert resumed and {r["extra"]["metadata"]["thread_id"] for r in resumed} == {"thr_1"}
