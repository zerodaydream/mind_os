"""Ports: the only contracts the inner layers know about adapters (ARCHITECTURE section 3).

Adapters implement these in `integrations/` or `agents/` and are wired in `bootstrap/`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from opsagent.domain.approvals import (
    ApprovalRecord,
    ApprovalTicket,
    EditVerdict,
    Resolution,
)
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

    def decide(
        self, *, tool: str, risk: RiskClass, principal: Principal, tainted: bool = False
    ) -> PolicyDecision: ...


class ApprovalRepository(Protocol):
    """Write and read approval rows. The only code that may touch the approvals table."""

    async def create(
        self,
        *,
        approval_id: str,
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        payload_hash: str,
        idempotency_key: str,
        args_json: str,
        summary: str,
        payload_preview: str,
        tainted: bool,
        expires_at: datetime,
        run_id: str | None,
        thread_id: str | None,
        conversation_id: str | None,
        tool_call_id: str | None,
    ) -> bool:
        """Insert a PENDING row. False if the idempotency key already exists (a replay)."""
        ...

    async def transition(
        self,
        approval_id: str,
        *,
        from_states: frozenset[str],
        to_state: str,
        actor: str | None = None,
        payload_hash: str | None = None,
        args_json: str | None = None,
        require_unexpired: bool = False,
    ) -> bool:
        """Atomically transition state. Returns True if exactly one row was updated.

        `require_unexpired` adds `expires_at > now()` to the same statement. Granting and
        consuming an approval must set it, so an expired row can never be used.
        """
        ...

    async def get(self, approval_id: str) -> ApprovalRecord | None: ...

    async def get_by_idempotency_key(self, key: str) -> ApprovalRecord | None: ...

    async def expire_stale(self) -> list[ApprovalRecord]:
        """Set PENDING+APPROVED+APPROVED_EDITED rows past expires_at to EXPIRED; return them."""
        ...

    async def abandon_consumed(self) -> list[ApprovalRecord]:
        """CONSUMED -> EXEC_FAILED for rows whose execution was cut off. Startup only."""
        ...

    async def undelivered(self) -> list[ApprovalRecord]:
        """Approvals whose paused run has not yet received the outcome (reconciliation worklist)."""
        ...

    async def mark_delivered(self, approval_id: str) -> None: ...


class ExecutionLog(Protocol):
    """`tool_executions`: durable evidence that a side effect started (ARCHITECTURE 4.2 step 8)."""

    async def start(
        self,
        *,
        execution_id: str,
        run_id: str | None,
        tool: str,
        agent: str,
        principal: Principal,
        risk: RiskClass,
        idempotency_key: str,
        args_hash: str,
    ) -> bool:
        """Record STARTED before the call. False if this idempotency key already ran."""
        ...

    async def finish(self, execution_id: str, *, ok: bool) -> None: ...

    async def abandon_started(self) -> list[tuple[str, str, str | None]]:
        """Mark every STARTED row UNKNOWN; return (id, tool, run_id). Startup only."""
        ...


class EditValidator(Protocol):
    """Re-validates and re-classifies edited arguments (implemented by the tool engine)."""

    async def revalidate(
        self, *, tool: str, principal: Principal, args: dict[str, Any]
    ) -> EditVerdict: ...


class ApprovalBrokerPort(Protocol):
    """Creates, tracks, and resolves approval requests. Used by the engine and the facade."""

    def bind_validator(self, validator: EditValidator) -> None:
        """Attach the engine's edit validator. It is built after the broker, so it binds late."""
        ...

    async def request(
        self,
        *,
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        args: dict[str, Any],
        run_id: str | None,
        tool_call_id: str,
        tainted: bool,
    ) -> ApprovalTicket: ...

    async def get(self, approval_id: str) -> ApprovalRecord | None: ...

    async def existing(
        self,
        *,
        tool: str,
        tool_version: str,
        principal: Principal,
        args: dict[str, Any],
        run_id: str | None,
        tool_call_id: str,
    ) -> ApprovalRecord | None:
        """The approval an earlier pass of this exact call already created, if any."""
        ...

    async def resolve(
        self,
        approval_id: str,
        *,
        decision: str,
        edited_args: dict[str, Any] | None = None,
        comment: str | None = None,
        actor: str = "USER",
    ) -> Resolution:
        """Apply a user decision (`approve`, `edit`, `reject`) and say what actually happened."""
        ...

    async def consume(self, approval_id: str) -> bool: ...

    async def mark_executed(self, approval_id: str, *, success: bool) -> None: ...

    async def invalidate(self, approval_id: str, *, reason: str) -> None: ...

    async def cancel_for_run(self, run_id: str) -> int: ...

    async def mark_delivered(self, approval_id: str) -> None: ...

    async def expire_stale(self) -> list[ApprovalRecord]: ...

    async def abandon_consumed(self) -> list[ApprovalRecord]: ...

    async def undelivered(self) -> list[ApprovalRecord]: ...
