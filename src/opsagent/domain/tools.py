"""Tool and subagent contracts (ARCHITECTURE sections 3.3 and 4)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from opsagent.domain.risk import Principal


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolIdempotency(StrEnum):
    READ_ONLY = "read_only"  # safe to repeat; the engine may retry transient failures
    IDEMPOTENT = "idempotent"  # repeating with the same key has no extra effect
    NON_IDEMPOTENT = "non_idempotent"  # never retried after an ambiguous failure


class Evidence(_Frozen):
    """A verifiable identifier for something a tool touched (event ID, message ID, path+hash)."""

    kind: str
    id: str


class ToolInvocation(_Frozen):
    """One request to run a tool. The model proposes it; the engine decides."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    principal: Principal = Principal.AGENT
    agent: str  # which agent asks; grants are per agent
    tool_call_id: str
    run_id: str | None = None
    approval_id: str | None = None  # set when resuming a call the user approved
    # An agent whose graph can pause (the orchestrator) waits for the user. One that cannot (a
    # subagent) gets its approval queued as deferred: the call does not run now, and the
    # application runs it if the user approves (ADR 0005).
    can_pause: bool = False


ToolStatus = Literal["ok", "error", "denied", "pending_approval"]


class ToolResult(_Frozen):
    """What the engine returns. `content` goes to the model; `preview` goes to the UI."""

    tool_call_id: str
    tool: str
    status: ToolStatus
    content: str
    preview: str
    error_code: str | None = None
    artifact_ref: str | None = None
    evidence: tuple[Evidence, ...] = ()
    duration_ms: int = 0
    approval_id: str | None = None  # set when status == "pending_approval"


class TaskBrief(_Frozen):
    """What an orchestrator hands to a subagent. Subagents never see the parent history."""

    goal: str = Field(min_length=1, max_length=2000)
    constraints: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    expected_result: str | None = None


SubagentStatus = Literal["success", "partial", "failed", "needs_input"]


class SubagentResult(_Frozen):
    """The only thing a subagent returns. Thoughts and message history stay inside it."""

    status: SubagentStatus
    summary: str
    evidence: tuple[Evidence, ...] = ()
    artifacts: tuple[str, ...] = ()
    pending_approvals: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    side_effects: tuple[str, ...] = ()  # effects the subagent claims; each needs evidence

    @model_validator(mode="after")
    def _success_with_effects_needs_evidence(self) -> SubagentResult:
        if self.status == "success" and self.side_effects and not self.evidence:
            raise ValueError("a success that claims side effects must carry evidence")
        return self
