"""Typed application events (ARCHITECTURE section 13.1).

Every event carries `schema_version`, `run_id`, `seq`, `ts`, and the correlation IDs that were
bound when it was created. The event bus stamps `seq` and `ts`. The UI renders events and
never imports business code.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from opsagent.domain.context import current
from opsagent.domain.reasoning import ReasoningEffort
from opsagent.domain.risk import ApprovalMode, RiskClass

SCHEMA_VERSION = 1


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION
    run_id: str | None = None
    seq: int = 0
    ts: datetime | None = None
    correlation: Mapping[str, str] = Field(default_factory=lambda: dict(current()))


# ---- run lifecycle ---------------------------------------------------------------------------
class RunStarted(_Event):
    type: Literal["run.started"] = "run.started"
    conversation_id: str
    thread_id: str
    user_text: str


class RunFinished(_Event):
    type: Literal["run.finished"] = "run.finished"
    summary: str | None = None


class RunFailed(_Event):
    type: Literal["run.failed"] = "run.failed"
    code: str
    message: str


class RunCancelled(_Event):
    type: Literal["run.cancelled"] = "run.cancelled"
    reason: str | None = None


# ---- assistant output ------------------------------------------------------------------------
class AssistantTextDelta(_Event):
    type: Literal["assistant.text.delta"] = "assistant.text.delta"
    message_id: str
    text: str


class AssistantMessageComplete(_Event):
    type: Literal["assistant.message.complete"] = "assistant.message.complete"
    message_id: str
    text: str


class ThinkingDelta(_Event):
    """Model thought summary. Untrusted content: never place it in audit bodies."""

    type: Literal["thinking.delta"] = "thinking.delta"
    message_id: str
    text: str


class ThinkingComplete(_Event):
    type: Literal["thinking.complete"] = "thinking.complete"
    message_id: str
    tokens: int | None = None


class Todo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    status: Literal["pending", "in_progress", "done"] = "pending"


class PlanUpdated(_Event):
    type: Literal["plan.updated"] = "plan.updated"
    todos: tuple[Todo, ...]


# ---- subagents and tools ---------------------------------------------------------------------
class SubagentStarted(_Event):
    type: Literal["subagent.started"] = "subagent.started"
    subagent_id: str
    subagent: str
    brief: str


class SubagentProgress(_Event):
    type: Literal["subagent.progress"] = "subagent.progress"
    subagent_id: str
    note: str


class SubagentFinished(_Event):
    type: Literal["subagent.finished"] = "subagent.finished"
    subagent_id: str
    status: Literal["success", "partial", "failed", "needs_input"]
    summary: str


class ToolCallStarted(_Event):
    type: Literal["tool.started"] = "tool.started"
    tool_call_id: str
    tool: str
    args_preview: str
    subagent_id: str | None = None


class ToolCallProgress(_Event):
    type: Literal["tool.progress"] = "tool.progress"
    tool_call_id: str
    note: str


class ToolCallFinished(_Event):
    type: Literal["tool.finished"] = "tool.finished"
    tool_call_id: str
    tool: str
    status: Literal["ok", "error", "rejected"]
    duration_ms: int | None = None
    result_preview: str | None = None
    artifact_ref: str | None = None


# ---- approvals and auth ----------------------------------------------------------------------
class ApprovalView(BaseModel):
    """What the user sees in the permission dialog. Built from the exact payload to be hashed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    approval_id: str
    tool: str
    risk: RiskClass
    summary: str
    payload_preview: str
    payload_hash: str
    tainted: bool = False
    expires_at: datetime | None = None


class ApprovalRequested(_Event):
    type: Literal["approval.requested"] = "approval.requested"
    approval: ApprovalView


class ApprovalResolved(_Event):
    type: Literal["approval.resolved"] = "approval.resolved"
    approval_id: str
    outcome: Literal["approved", "edited", "rejected", "invalidated"]


class ApprovalExpired(_Event):
    type: Literal["approval.expired"] = "approval.expired"
    approval_id: str


class AuthRequired(_Event):
    type: Literal["auth.required"] = "auth.required"
    provider: str
    scope_delta: tuple[str, ...] = ()


class AuthRestored(_Event):
    type: Literal["auth.restored"] = "auth.restored"
    provider: str


# ---- background and health -------------------------------------------------------------------
class JobEvent(_Event):
    type: Literal["job"] = "job"
    job_id: str
    state: str
    detail: str | None = None


class Notification(_Event):
    type: Literal["notification"] = "notification"
    level: Literal["info", "warning", "error"] = "info"
    text: str


class Degraded(_Event):
    type: Literal["degraded"] = "degraded"
    component: str
    detail: str


class Recovered(_Event):
    type: Literal["recovered"] = "recovered"
    component: str


class ErrorEvent(_Event):
    type: Literal["error"] = "error"
    code: str
    message: str
    remediation: str | None = None


# ---- session UI state ------------------------------------------------------------------------
class UsageUpdated(_Event):
    type: Literal["usage.updated"] = "usage.updated"
    scope: Literal["run", "session"] = "run"
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    context_pct: float | None = None
    cost_usd: float | None = None
    reasoning_effort: ReasoningEffort | None = None


class ModeChanged(_Event):
    type: Literal["mode.changed"] = "mode.changed"
    mode: ApprovalMode


class CommandOutput(_Event):
    type: Literal["command.output"] = "command.output"
    command: str
    text: str


class Rewound(_Event):
    type: Literal["rewound"] = "rewound"
    message_id: str


AppEvent = Annotated[
    RunStarted
    | RunFinished
    | RunFailed
    | RunCancelled
    | AssistantTextDelta
    | AssistantMessageComplete
    | ThinkingDelta
    | ThinkingComplete
    | PlanUpdated
    | SubagentStarted
    | SubagentProgress
    | SubagentFinished
    | ToolCallStarted
    | ToolCallProgress
    | ToolCallFinished
    | ApprovalRequested
    | ApprovalResolved
    | ApprovalExpired
    | AuthRequired
    | AuthRestored
    | JobEvent
    | Notification
    | Degraded
    | Recovered
    | ErrorEvent
    | UsageUpdated
    | ModeChanged
    | CommandOutput
    | Rewound,
    Field(discriminator="type"),
]

TERMINAL_EVENTS = (RunFinished, RunFailed, RunCancelled)

_ADAPTER: TypeAdapter[Any] = TypeAdapter(AppEvent)


def parse_event(data: str | bytes | Mapping[str, Any]) -> _Event:
    """Rebuild an event from JSON text or a dict. Raises `pydantic.ValidationError`."""
    if isinstance(data, str | bytes):
        result: _Event = _ADAPTER.validate_json(data)
    else:
        result = _ADAPTER.validate_python(dict(data))
    return result
