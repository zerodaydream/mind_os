"""Composition root: the only place that wires adapters to ports."""

from __future__ import annotations

from dataclasses import dataclass

from opsagent.agents import UnavailableRuntime
from opsagent.application import Application, EventBus
from opsagent.domain import AgentRuntime, CredentialProvider, LLMProvider
from opsagent.infra import Settings, SettingsCredentialProvider, SystemClock, new_id
from opsagent.integrations.llm import GeminiProvider


@dataclass(frozen=True)
class Container:
    settings: Settings
    credentials: CredentialProvider
    llm: LLMProvider
    bus: EventBus
    app: Application


def build_container(settings: Settings, *, runtime: AgentRuntime | None = None) -> Container:
    """Wire the application. Pass `runtime` to substitute a fake in tests."""
    credentials = SettingsCredentialProvider(settings)
    llm = GeminiProvider(
        models=settings.models(),
        credentials=credentials,
        include_thoughts=settings.include_thoughts,
        thinking_budget=settings.thinking_budget,
    )
    bus = EventBus(SystemClock())
    app = Application(runtime=runtime or UnavailableRuntime(), bus=bus, new_id=new_id)
    return Container(settings=settings, credentials=credentials, llm=llm, bus=bus, app=app)
