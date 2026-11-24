"""Wires tools, engine, subagents, and the runtime. Called once by `bootstrap`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.checkpoint.base import BaseCheckpointSaver

from opsagent.agents.hub import RunHub
from opsagent.agents.limits import RunLimits
from opsagent.agents.runtime import ORCHESTRATOR, LangChainRuntime
from opsagent.agents.subagents import CALENDAR, ChatModels, SubagentRunner, delegate_spec
from opsagent.agents.usage import UsageTracker
from opsagent.domain import (
    ApprovalBrokerPort,
    ArtifactStore,
    AuditSink,
    CalendarReader,
    Clock,
    ExecutionLog,
    IdFactory,
    ModelRole,
    PolicyEngine,
    Pricing,
    ReasoningEffort,
    Tracing,
    UsageSink,
)
from opsagent.tools import (
    WRITE_TODOS,
    ToolExecutionEngine,
    ToolRegistry,
    calendar_specs,
    read_artifact_spec,
)

ORCHESTRATOR_TOOLS = frozenset({"delegate", "write_todos", "read_artifact"})


def build_runtime(
    *,
    models: ChatModels,
    calendar: CalendarReader,
    tz: ZoneInfo,
    audit: AuditSink,
    artifacts: ArtifactStore,
    usage: UsageSink,
    pricing: Pricing | None,
    policy: PolicyEngine,
    checkpointer: BaseCheckpointSaver[Any],
    limits: RunLimits,
    clock: Clock,
    new_id: IdFactory,
    efforts: Mapping[ModelRole, ReasoningEffort],
    tracing: Tracing | None = None,
    broker: ApprovalBrokerPort | None = None,
    executions: ExecutionLog | None = None,
) -> LangChainRuntime:
    hub = RunHub()
    tracker = UsageTracker(sink=usage, pricing=pricing)
    runner = SubagentRunner(
        definitions=(CALENDAR,),
        models=models,
        hub=hub,
        tracker=tracker,
        clock=clock,
        tz=tz,
        new_id=new_id,
        efforts=dict(efforts),
    )
    registry = ToolRegistry(
        [
            WRITE_TODOS,
            read_artifact_spec(artifacts),
            *calendar_specs(calendar, tz),
            delegate_spec(runner, timeout_s=limits.deadline_s),
        ]
    )
    grants = {ORCHESTRATOR: ORCHESTRATOR_TOOLS, **{d.name: d.tools for d in runner.definitions}}
    engine = ToolExecutionEngine(
        registry=registry,
        grants=grants,
        policy=policy,
        audit=audit,
        artifacts=artifacts,
        broker=broker,
        executions=executions,
        new_id=new_id,
    )
    runner.bind_engine(engine)
    return LangChainRuntime(
        models=models,
        engine=engine,
        hub=hub,
        tracker=tracker,
        runner=runner,
        checkpointer=checkpointer,
        limits=limits,
        clock=clock,
        tz=tz,
        new_id=new_id,
        default_effort=efforts.get(ModelRole.ORCHESTRATOR, ReasoningEffort.HIGH),
        tracing=tracing,
    )
