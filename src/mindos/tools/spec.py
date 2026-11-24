"""Tool specification and registry (ARCHITECTURE section 4.2, stage 1)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from mindos.domain import (
    BoundState,
    EventEmit,
    Evidence,
    Principal,
    RiskClass,
    TaintSource,
    ToolIdempotency,
)

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$")
CORE_NAMESPACE = "core"


class ToolError(Exception):
    """A failure whose message is safe and useful to show the model."""

    def __init__(self, message: str, *, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code


class TransientToolError(ToolError):
    """A failure worth retrying (rate limit, brief network error). Only read tools are retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="transient_error")


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a handler may know about the call. It holds no adapters and no secrets."""

    run_id: str | None
    agent: str
    principal: Principal
    tool_call_id: str
    emit: EventEmit


Handler = Callable[[Any, ToolContext], Awaitable[Any]]
Classifier = Callable[[Any], RiskClass]
EvidenceFn = Callable[[Any], Iterable[Evidence]]
BindState = Callable[[Any], Awaitable[BoundState]]


@dataclass(frozen=True)
class ToolSpec:
    """Everything the engine needs to run a tool safely. `handler` gets validated arguments."""

    name: str
    version: str
    summary: str
    input_model: type[BaseModel]
    handler: Handler
    default_risk: RiskClass = RiskClass.R0
    classify: Classifier | None = None
    idempotency: ToolIdempotency = ToolIdempotency.READ_ONLY
    # True for a tool that only coordinates other tools (`delegate`). Its effects are the effects
    # of the tools it calls, each recorded on its own, so it is not itself a logged side effect.
    orchestration: bool = False
    timeout_s: float = 20.0
    untrusted_output: bool = False  # True when output carries text written by other people
    taint_source: TaintSource | None = None  # what taints a run; default TOOL_OUTPUT if untrusted
    max_result_chars: int = 6000
    evidence: EvidenceFn | None = None
    # Resolves what the arguments point at and fingerprints its state (a file's real path and
    # content hash). The approval binds to it; if it drifts before execution, the approval is void.
    # A handler must still re-check at the moment it acts.
    bind_state: BindState | None = None
    namespace: str = field(init=False)

    def __post_init__(self) -> None:
        if not TOOL_NAME.match(self.name):
            raise ValueError(f"invalid tool name {self.name!r}")
        namespace = self.name.split(".")[0] if "." in self.name else CORE_NAMESPACE
        object.__setattr__(self, "namespace", namespace)


class ToolRegistry:
    """Holds every tool. A duplicate name fails startup."""

    def __init__(self, specs: Iterable[ToolSpec] = ()) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool name {spec.name!r}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._specs)

    def __len__(self) -> int:
        return len(self._specs)
