"""Composition root: the only place that wires adapters to ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from opsagent.agents import RunLimits, UnavailableRuntime, build_runtime
from opsagent.application import Application, EventBus
from opsagent.domain import AgentRuntime, CredentialProvider, LLMProvider, ModelRole
from opsagent.infra import (
    Settings,
    SettingsCredentialProvider,
    SystemClock,
    new_id,
    resolve_timezone,
)
from opsagent.integrations.calendar import FixtureCalendar, sample_events
from opsagent.integrations.llm import GeminiProvider
from opsagent.persistence import ArtifactRepository, AuditRepository, UsageRepository
from opsagent.persistence.db import Pool
from opsagent.tools import ReadOnlyPolicy


@dataclass(frozen=True)
class Container:
    settings: Settings
    credentials: CredentialProvider
    llm: LLMProvider
    bus: EventBus
    app: Application


def build_container(
    settings: Settings,
    *,
    runtime: AgentRuntime | None = None,
    pool: Pool | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> Container:
    """Wire the application.

    Pass `runtime` to substitute a fake. Without one, a real agent runtime is built when both
    `pool` and `checkpointer` are given; otherwise runs fail with a clear message.
    """
    credentials = SettingsCredentialProvider(settings)
    clock = SystemClock()
    llm = GeminiProvider(
        models=settings.models(),
        credentials=credentials,
        include_thoughts=settings.include_thoughts,
        thinking_budget=settings.thinking_budget,
    )
    if runtime is None and pool is not None and checkpointer is not None:
        tz = resolve_timezone(settings.timezone)
        runtime = build_runtime(
            models=llm,
            calendar=FixtureCalendar(sample_events(clock.now(), tz)),  # Google: Phase 06
            tz=tz,
            audit=AuditRepository(pool),
            artifacts=ArtifactRepository(pool, new_id),
            usage=UsageRepository(pool),
            pricing=settings.pricing(),
            policy=ReadOnlyPolicy(),  # real policy engine and approvals: Phase 05
            checkpointer=checkpointer,
            limits=RunLimits(
                max_steps=settings.max_agent_steps,
                subagent_steps=settings.subagent_max_steps,
                max_tool_calls=settings.max_tool_calls,
                deadline_s=float(settings.run_deadline_seconds),
                max_parallel_subagents=settings.max_parallel_subagents,
            ),
            clock=clock,
            new_id=new_id,
            efforts={role: settings.reasoning_for(role) for role in ModelRole},
        )
    bus = EventBus(clock)
    app = Application(runtime=runtime or UnavailableRuntime(), bus=bus, new_id=new_id)
    return Container(settings=settings, credentials=credentials, llm=llm, bus=bus, app=app)
