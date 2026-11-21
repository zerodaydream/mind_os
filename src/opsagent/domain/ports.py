"""Ports: the only contracts the inner layers know about adapters (ARCHITECTURE section 3).

Adapters implement these in `integrations/` or `agents/` and are wired in `bootstrap/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from opsagent.domain.commands import Decide
from opsagent.domain.events import AppEvent
from opsagent.domain.llm import LLMRequest, LLMResult, ModelCapabilities, ModelRole

# Credential names understood by `CredentialProvider.get`.
GEMINI_API_KEY = "gemini_api_key"

IdFactory = Callable[[str], str]
"""`new_id("run")` returns a sortable unique ID such as `run_01J...`."""


class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class CredentialProvider(Protocol):
    """Secrets come from here only. Values must never be logged or placed in events."""

    def get(self, name: str) -> str | None: ...


class RunSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    conversation_id: str
    thread_id: str
    user_text: str


class LLMProvider(Protocol):
    def capabilities(self, role: ModelRole) -> ModelCapabilities: ...

    async def complete(self, request: LLMRequest) -> LLMResult: ...


class AgentRuntime(Protocol):
    """The seam over DeepAgents or plain LangGraph (ADR 0001).

    Implementations stream application events. They may emit a terminal event
    (RunFinished, RunFailed, RunCancelled); if they do not, the facade adds RunFinished.
    """

    def start(self, run: RunSpec) -> AsyncIterator[AppEvent]: ...

    def resume(self, thread_id: str, decision: Decide) -> AsyncIterator[AppEvent]: ...

    async def cancel(self, run_id: str) -> None: ...
