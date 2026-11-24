"""Wires tools, engine, subagents, and the runtime. Called once by `bootstrap`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.checkpoint.base import BaseCheckpointSaver

from mindos.agents.hub import RunHub
from mindos.agents.limits import RunLimits
from mindos.agents.runtime import ORCHESTRATOR, LangChainRuntime
from mindos.agents.subagents import (
    ChatModels,
    SubagentRunner,
    calendar_definition,
    delegate_spec,
)
from mindos.agents.tuning import ModelCatalog, ModelTuner
from mindos.agents.usage import UsageTracker
from mindos.domain import (
    ApprovalBrokerPort,
    ArtifactStore,
    AuditSink,
    CalendarReader,
    CalendarWriter,
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
from mindos.tools import (
    WRITE_TODOS,
    ToolExecutionEngine,
    ToolRegistry,
    calendar_specs,
    calendar_write_specs,
    read_artifact_spec,
)

ORCHESTRATOR_TOOLS = frozenset({"delegate", "write_todos", "read_artifact"})


def build_runtime(
    *,
    models: ChatModels,
    calendar: CalendarReader,
    writer: CalendarWriter | None = None,
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
    catalog: ModelCatalog | None = None,
    extra_models: tuple[str, ...] = (),
    tracing: Tracing | None = None,
    broker: ApprovalBrokerPort | None = None,
    executions: ExecutionLog | None = None,
) -> LangChainRuntime:
    tuner = ModelTuner(
        efforts=dict(efforts),
        model_id=models.model_id,
        catalog=catalog,
        extra_models=extra_models,
    )
    hub = RunHub()
    tracker = UsageTracker(sink=usage, pricing=pricing)
    runner = SubagentRunner(
        definitions=(calendar_definition(can_write=writer is not None),),
        models=models,
        hub=hub,
        tracker=tracker,
        clock=clock,
        tz=tz,
        new_id=new_id,
        tuner=tuner,
    )
    registry = ToolRegistry(
        [
            WRITE_TODOS,
            read_artifact_spec(artifacts),
            *calendar_specs(calendar, tz),
            *(calendar_write_specs(calendar, writer, tz) if writer else ()),
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
        tuner=tuner,
        tracing=tracing,
    )
