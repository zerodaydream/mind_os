"""Composition root: the only place that wires adapters to ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from mindos import __version__
from mindos.agents import LangChainRuntime, RunLimits, UnavailableRuntime, build_runtime
from mindos.application import Application, ApprovalBroker, EventBus
from mindos.bootstrap.auth import AuthKit, build_auth
from mindos.bootstrap.credentials import ChainedCredentials
from mindos.domain import (
    LANGSMITH_API_KEY,
    AgentRuntime,
    ApprovalBrokerPort,
    AuditSink,
    Calendar,
    CredentialProvider,
    ExecutionLog,
    LLMProvider,
    ModelRole,
    SessionMode,
    Tracing,
)
from mindos.infra import (
    Settings,
    SettingsCredentialProvider,
    SystemClock,
    new_id,
    resolve_timezone,
)
from mindos.integrations.auth import KeychainCredentialProvider, KeychainSecretStore
from mindos.integrations.calendar import FixtureCalendar, sample_events
from mindos.integrations.google import GoogleCalendar
from mindos.integrations.llm import GeminiProvider
from mindos.integrations.tracing import LangSmithTracing, TracingConfig
from mindos.persistence import (
    ApprovalRepository,
    ArtifactRepository,
    AuditRepository,
    ExecutionRepository,
    UsageRepository,
)
from mindos.persistence.db import Pool
from mindos.tools import PolicyEngine, load_policy


@dataclass(frozen=True)
class Container:
    settings: Settings
    credentials: CredentialProvider
    llm: LLMProvider
    bus: EventBus
    app: Application
    # Present only when the real runtime is wired; startup reconciliation needs them.
    broker: ApprovalBrokerPort | None = None
    executions: ExecutionLog | None = None
    audit: AuditSink | None = None
    tracing: Tracing | None = None
    auth: AuthKit | None = None  # holds the HTTP client; close it on exit


def build_tracing(settings: Settings, credentials: CredentialProvider) -> LangSmithTracing:
    """Tracing from our settings only. The Keychain key wins over the settings value."""
    fallback = settings.langsmith_api_key
    key = credentials.get(LANGSMITH_API_KEY) or (fallback.get_secret_value() if fallback else None)
    return LangSmithTracing(
        TracingConfig(
            enabled=settings.langsmith_tracing,
            api_key=key,
            project=settings.langsmith_project,
            agent_id=settings.langsmith_agent_id,
            environment=settings.langsmith_environment,
            endpoint=settings.langsmith_endpoint,
            workspace_id=settings.langsmith_workspace_id,
            hide_io=settings.langsmith_hide_io,
            sampling_rate=settings.langsmith_sampling_rate,
            version=__version__,
        )
    )


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
    # The Keychain wins; the settings value is a development fallback.
    credentials = ChainedCredentials(
        KeychainCredentialProvider(KeychainSecretStore()), SettingsCredentialProvider(settings)
    )
    clock = SystemClock()
    llm = GeminiProvider(
        models=settings.models(),
        credentials=credentials,
        include_thoughts=settings.include_thoughts,
        thinking_budget=settings.thinking_budget,
    )
    broker: ApprovalBrokerPort | None = None
    executions: ExecutionLog | None = None
    audit: AuditSink | None = None
    tracing = build_tracing(settings, credentials)
    auth: AuthKit | None = None
    mode = SessionMode()  # shared: the UI sets it, the policy engine enforces it
    if runtime is None and pool is not None and checkpointer is not None:
        tz = resolve_timezone(settings.timezone)
        audit = AuditRepository(pool)
        executions = ExecutionRepository(pool)
        broker = ApprovalBroker(
            repo=ApprovalRepository(pool), audit=audit, new_id=new_id, clock=clock
        )
        # An unreadable policy.toml raises. It must never fall back to the compiled defaults.
        policy = PolicyEngine(load_policy(settings.config_dir / "policy.toml"), mode)
        # Google when a client ID is configured (not signed in: the tools say how to sign in),
        # otherwise a sample calendar so the agent has something to work on.
        auth = build_auth(settings, pool, clock)
        calendar: Calendar
        if "google" in auth.clients:
            calendar = GoogleCalendar(tokens=auth.tokens, states=auth.states, http=auth.http, tz=tz)
        else:
            calendar = FixtureCalendar(sample_events(clock.now(), tz))
        runtime = build_runtime(
            models=llm,
            calendar=calendar,
            writer=calendar,
            tz=tz,
            audit=audit,
            artifacts=ArtifactRepository(pool, new_id),
            usage=UsageRepository(pool),
            pricing=settings.pricing(),
            policy=policy,
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
            catalog=llm,
            extra_models=settings.extra_models(),
            tracing=tracing,
            broker=broker,
            executions=executions,
        )
    bus = EventBus(clock)
    app = Application(
        runtime=runtime or UnavailableRuntime(),
        bus=bus,
        new_id=new_id,
        broker=broker,
        executor=runtime if isinstance(runtime, LangChainRuntime) else None,
        tuning=runtime.tuner if isinstance(runtime, LangChainRuntime) else None,
        mode=mode,
    )
    return Container(
        settings=settings,
        credentials=credentials,
        llm=llm,
        bus=bus,
        app=app,
        broker=broker,
        executions=executions,
        audit=audit,
        tracing=tracing,
        auth=auth,
    )
