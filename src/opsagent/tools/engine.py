"""The tool execution engine: the only path from a model's request to an integration adapter.

Pipeline (ARCHITECTURE section 4.2): look up, check permission, validate, classify risk,
decide policy, execute, normalize, audit. Every failure becomes a structured `ToolResult`
the model can read. Nothing here trusts what the model says about risk or permission.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from pydantic import BaseModel, ValidationError

from opsagent.domain import (
    ArtifactStore,
    AuditSink,
    EventEmit,
    Evidence,
    PolicyDecision,
    PolicyEngine,
    RiskClass,
    ToolIdempotency,
    ToolInvocation,
    ToolResult,
    ToolStatus,
    bind,
    redact,
    redact_text,
)
from opsagent.tools.normalize import bound, preview, serialize, wrap_untrusted
from opsagent.tools.spec import ToolContext, ToolError, ToolRegistry, ToolSpec, TransientToolError

log = logging.getLogger(__name__)

_ARGS_PREVIEW_CHARS = 200
_ERROR_CHARS = 500
_RETRY_BASE_S = 0.5
_RETRY_CAP_S = 4.0

Sleep = Callable[[float], Awaitable[None]]
Admit = Callable[[], str | None]


def _noop_emit(_event: Any) -> None:
    return None


class ToolExecutionEngine:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        grants: Mapping[str, frozenset[str]],
        policy: PolicyEngine,
        audit: AuditSink,
        artifacts: ArtifactStore,
        max_attempts: int = 3,
        sleep: Sleep = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        for agent, names in grants.items():
            unknown = names - registry.names()
            if unknown:
                raise ValueError(f"grants for {agent!r} name unknown tools: {sorted(unknown)}")
        self._registry = registry
        self._grants = {agent: frozenset(names) for agent, names in grants.items()}
        self._policy = policy
        self._audit = audit
        self._artifacts = artifacts
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter

    def granted(self, agent: str) -> frozenset[str]:
        """Tool names an agent may see and call. Unknown agents get nothing."""
        return self._grants.get(agent, frozenset())

    def spec(self, name: str) -> ToolSpec:
        spec = self._registry.get(name)
        if spec is None:
            raise KeyError(name)
        return spec

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        emit: EventEmit = _noop_emit,
        admit: Admit | None = None,
    ) -> ToolResult:
        """Run one invocation through the full pipeline. Never raises for tool-level failures.

        `admit` charges a run-level budget. It returns a refusal message, or None to proceed.
        """
        started = time.monotonic()
        with bind(run_id=invocation.run_id, tool_call_id=invocation.tool_call_id):
            gate = await self._gate(invocation, started, admit)
            if isinstance(gate, ToolResult):
                return gate
            spec, args, risk = gate
            ctx = ToolContext(
                run_id=invocation.run_id,
                agent=invocation.agent,
                principal=invocation.principal,
                tool_call_id=invocation.tool_call_id,
                emit=emit,
            )

            def fail(status: ToolStatus, code: str, message: str) -> Awaitable[ToolResult]:
                return self._fail(
                    invocation, spec, started, status=status, code=code, message=message, risk=risk
                )

            try:
                raw = await self._run(spec, args, ctx)
            except TimeoutError:
                return await fail(
                    "error", "timeout", f"'{spec.name}' timed out after {spec.timeout_s:g}s."
                )
            except ToolError as exc:
                return await fail("error", exc.code, redact_text(str(exc))[:_ERROR_CHARS])
            except Exception:
                log.exception("tool %s crashed", spec.name)  # details stay in the log
                return await fail("error", "internal_error", f"'{spec.name}' failed unexpectedly.")
            return await self._succeed(invocation, spec, raw, risk, started)

    async def _gate(
        self, inv: ToolInvocation, started: float, admit: Admit | None
    ) -> tuple[ToolSpec, BaseModel, RiskClass] | ToolResult:
        """Stages 1-5: lookup, permission, validation, risk, policy. A ToolResult means refused."""
        spec = self._registry.get(inv.tool)
        if spec is None:
            return await self._fail(
                inv, None, started,
                status="denied", code="unknown_tool",
                message=f"There is no tool named '{inv.tool}'.",
            )  # fmt: skip
        if inv.tool not in self.granted(inv.agent):
            return await self._fail(
                inv, spec, started,
                status="denied", code="not_permitted",
                message=f"Tool '{spec.name}' is not available to this agent.",
            )  # fmt: skip
        refusal = admit() if admit is not None else None
        if refusal is not None:
            return await self._fail(
                inv, spec, started, status="denied", code="budget_exhausted", message=refusal
            )
        try:
            args = spec.input_model.model_validate(inv.args)
        except ValidationError as exc:
            return await self._fail(
                inv, spec, started,
                status="error", code="invalid_arguments",
                message=f"Invalid arguments: {_describe(exc)} Fix them and try again.",
            )  # fmt: skip
        risk = spec.classify(args) if spec.classify else spec.default_risk
        if self._policy.decide(tool=spec.name, risk=risk, principal=inv.principal) is not (
            PolicyDecision.ALLOW
        ):
            return await self._fail(
                inv, spec, started,
                status="denied", code="policy_denied", risk=risk,
                message=(
                    f"Policy does not allow '{spec.name}' (risk {risk.value}) right now. "
                    "Nothing was executed. Tell the user instead of retrying."
                ),
            )  # fmt: skip
        return spec, args, risk

    # ---- stages ----------------------------------------------------------------------------
    async def _run(self, spec: ToolSpec, args: BaseModel, ctx: ToolContext) -> Any:
        attempts = self._max_attempts if spec.idempotency is ToolIdempotency.READ_ONLY else 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(spec.timeout_s):
                    return await spec.handler(args, ctx)
            except TransientToolError:
                if attempt + 1 >= attempts:
                    raise
                delay = self._jitter() * min(_RETRY_CAP_S, _RETRY_BASE_S * 2**attempt)
                log.warning("tool %s transient failure, retrying in %.2fs", spec.name, delay)
                await self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _succeed(
        self, inv: ToolInvocation, spec: ToolSpec, raw: Any, risk: RiskClass, started: float
    ) -> ToolResult:
        text = serialize(raw)
        content, artifact_ref = await bound(
            text, limit=spec.max_result_chars, store=self._artifacts
        )
        if spec.untrusted_output:
            content = wrap_untrusted(content, spec.name)
        evidence = _evidence(spec, raw)
        result = ToolResult(
            tool_call_id=inv.tool_call_id,
            tool=spec.name,
            status="ok",
            content=content,
            preview=preview(text),
            artifact_ref=artifact_ref,
            evidence=evidence,
            duration_ms=_elapsed_ms(started),
        )
        await self._record(inv, spec, risk, result, result_chars=len(text))
        return result

    async def _fail(
        self,
        inv: ToolInvocation,
        spec: ToolSpec | None,
        started: float,
        *,
        status: ToolStatus,
        code: str,
        message: str,
        risk: RiskClass | None = None,
    ) -> ToolResult:
        result = ToolResult(
            tool_call_id=inv.tool_call_id,
            tool=inv.tool,
            status=status,
            content=f"Error [{code}]: {message}",
            preview=preview(message),
            error_code=code,
            duration_ms=_elapsed_ms(started),
        )
        await self._record(inv, spec, risk, result, result_chars=0)
        return result

    async def _record(
        self,
        inv: ToolInvocation,
        spec: ToolSpec | None,
        risk: RiskClass | None,
        result: ToolResult,
        *,
        result_chars: int,
    ) -> None:
        """Write the audit record. A failed write is logged; it never hides the tool result."""
        event_type = {"ok": "tool.executed", "denied": "tool.denied", "error": "tool.failed"}[
            result.status
        ]
        canonical = json.dumps(inv.args, sort_keys=True, default=str, separators=(",", ":"))
        data: dict[str, Any] = {
            "tool_call_id": inv.tool_call_id,
            "agent": inv.agent,
            "risk": risk.value if risk else None,
            "status": result.status,
            "error_code": result.error_code,
            "duration_ms": result.duration_ms,
            "args_hash": hashlib.sha256(canonical.encode()).hexdigest(),
            "args_preview": preview(json.dumps(redact(inv.args), default=str), _ARGS_PREVIEW_CHARS),
            "result_chars": result_chars,
            "artifact_ref": result.artifact_ref,
            "evidence": [e.id for e in result.evidence],
            "untrusted_output": bool(spec and spec.untrusted_output),
        }
        try:
            await self._audit.append(
                event_type=event_type,
                actor=inv.principal.value,
                subject_type="tool",
                subject_id=inv.tool,
                data=data,
            )
        except Exception:
            log.exception("audit write failed for %s", inv.tool)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _describe(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False, include_input=False, include_context=False):
        where = ".".join(str(p) for p in err["loc"]) or "arguments"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)[:_ERROR_CHARS]


def _evidence(spec: ToolSpec, raw: Any) -> tuple[Evidence, ...]:
    if spec.evidence is None:
        return ()
    try:
        return tuple(spec.evidence(raw))
    except Exception:
        log.exception("evidence extraction failed for %s", spec.name)
        return ()
