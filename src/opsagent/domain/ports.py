"""Ports: the only contracts the inner layers know about adapters (ARCHITECTURE section 3).

Adapters implement these in `integrations/` or `agents/` and are wired in `bootstrap/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from opsagent.domain.commands import Decide
from opsagent.domain.events import AppEvent
from opsagent.domain.llm import LLMRequest, LLMResult, ModelCapabilities, ModelRole, Usage
from opsagent.domain.reasoning import ReasoningEffort
from opsagent.domain.risk import PolicyDecision, Principal, RiskClass

# Credential names understood by `CredentialProvider.get`.
GEMINI_API_KEY = "gemini_api_key"

IdFactory = Callable[[str], str]
"""`new_id("run")` returns a sortable unique ID such as `run_01J...`."""

EventEmit = Callable[[AppEvent], None]
"""Hands an event to whoever streams the current run. Must not block."""


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
    effort: ReasoningEffort | None = None  # session override from `/think`; None: use config


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


class AuditSink(Protocol):
    """Append-only audit trail. Implementations redact bodies before storing them."""

    async def append(
        self,
        *,
        event_type: str,
        actor: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int: ...


class ArtifactSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    offset: int
    total: int  # total characters stored

    @property
    def next_offset(self) -> int | None:
        end = self.offset + len(self.text)
        return end if end < self.total else None


class ArtifactStore(Protocol):
    """Holds tool results too large for the model context; the model reads them by slice."""

    async def put(self, content: str, *, media_type: str = "text/plain") -> str:
        """Store `content` and return its artifact ID."""
        ...

    async def read(self, artifact_id: str, *, offset: int, limit: int) -> ArtifactSlice | None: ...


class UsageSink(Protocol):
    """Records one model call for cost and token accounting."""

    async def record(
        self,
        *,
        role: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
        cost_usd: float | None,
    ) -> None: ...


class PolicyEngine(Protocol):
    """Decides whether a classified tool call may run. It can only be tightened, never bypassed."""

    def decide(self, *, tool: str, risk: RiskClass, principal: Principal) -> PolicyDecision: ...
