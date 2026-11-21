"""Model-facing value objects. No provider types appear here (MISSION principle 7)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from opsagent.domain.reasoning import ReasoningEffort


class ModelRole(StrEnum):
    ORCHESTRATOR = "orchestrator"
    SUBAGENT = "subagent"
    UTILITY = "utility"  # routing, titles, summaries


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelCapabilities(_Frozen):
    model_id: str
    effort_levels: tuple[ReasoningEffort, ...] = ()  # empty: no configurable reasoning
    default_effort: ReasoningEffort | None = None


class Usage(_Frozen):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)


class LLMRequest(_Frozen):
    role: ModelRole
    prompt: str
    system: str | None = None
    effort: ReasoningEffort | None = None  # None: provider default


class LLMResult(_Frozen):
    text: str
    thinking: str | None = None
    usage: Usage = Usage()
    model_id: str
    effort_applied: ReasoningEffort | None = None
