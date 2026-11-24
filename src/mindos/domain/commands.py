"""Commands the UI sends to the application facade (ARCHITECTURE section 13.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from opsagent.domain.reasoning import ReasoningEffort
from opsagent.domain.risk import ApprovalMode


class _Command(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubmitMessage(_Command):
    text: str
    conversation_id: str | None = None  # None starts a new conversation
    effort: ReasoningEffort | None = None  # session `/think` override


class Decide(_Command):
    approval_id: str
    decision: Literal["approve", "edit", "reject"]
    edited_args: dict[str, Any] | None = None
    comment: str | None = None


class Cancel(_Command):
    run_id: str


class Resume(_Command):
    thread_id: str


class Retry(_Command):
    run_id: str


class Rewind(_Command):
    message_id: str


class SetMode(_Command):
    mode: ApprovalMode


class RunSlashCommand(_Command):
    name: str
    args: str = ""
