"""The tool execution engine: the only path from a model's request to an integration adapter.

Pipeline (ARCHITECTURE section 4.2): look up, check permission, validate, classify risk,
decide policy, execute, normalize, audit. Every failure becomes a structured `ToolResult`
the model can read. Nothing here trusts what the model says about risk or permission.

Approvals (section 5): when policy says REQUIRE_APPROVAL the engine creates the approval and
returns `pending_approval`. The agent framework only pauses and resumes; the engine decides on
its own whether an approved call may run. On the approved path it takes the arguments from the
approval record (never from the caller), re-validates and re-classifies them, re-checks policy,
recomputes the payload hash, and only then claims the approval atomically. The approved
operation is exactly the executed operation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from opsagent.domain import (
    ApprovalBrokerPort,
    ApprovalRecord,
    ArtifactStore,
    AuditSink,
    EditVerdict,
    EventEmit,
    Evidence,
    ExecutionLog,
    IdFactory,
    PolicyDecision,
    PolicyEngine,
    Principal,
    RiskClass,
    TaintLedger,
    TaintSource,
    ToolIdempotency,
    ToolInvocation,
    ToolResult,
    ToolStatus,
    approval_idempotency_key,
    approval_payload_hash,
    bind,
    events,
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


@dataclass(frozen=True)
class _Gate:
    """A call that passed stages 1-7. `claim` is the approval this call consumed, if any."""

    spec: ToolSpec
    args: BaseModel
    risk: RiskClass
    claim: str | None = None


@dataclass(frozen=True)
class _Call:
    """What every gate step needs to know about the call in flight."""

    inv: ToolInvocation
    spec: ToolSpec
    started: float
    emit: EventEmit


def _normalized(args: BaseModel) -> dict[str, Any]:
    """The one form of arguments that is hashed, stored, previewed, and compared."""
    return args.model_dump(mode="json")


def _new_uuid_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ToolExecutionEngine:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        grants: Mapping[str, frozenset[str]],
        policy: PolicyEngine,
        audit: AuditSink,
        artifacts: ArtifactStore,
        broker: ApprovalBrokerPort | None = None,
        taint: TaintLedger | None = None,
        executions: ExecutionLog | None = None,
        new_id: IdFactory = _new_uuid_id,
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
        self._broker = broker
        self._taint = taint or TaintLedger()
        self._executions = executions
        self._new_id = new_id
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._jitter = jitter
        if broker is not None:
            broker.bind_validator(self)

    def granted(self, agent: str) -> frozenset[str]:
        """Tool names an agent may see and call. Unknown agents get nothing."""
        return self._grants.get(agent, frozenset())

    def spec(self, name: str) -> ToolSpec:
        spec = self._registry.get(name)
        if spec is None:
            raise KeyError(name)
        return spec

    def end_run(self, run_id: str | None) -> None:
        """A run finished or suspended: drop its in-memory taint. A resume re-reads it."""
        self._taint.forget(run_id)

    async def revalidate(
        self, *, tool: str, principal: Principal, args: dict[str, Any]
    ) -> EditVerdict:
        """May edited arguments replace the approved ones? Used by the broker for `edit`."""
        spec = self._registry.get(tool)
        if spec is None:
            return EditVerdict(ok=False, reason=f"no tool named '{tool}'")
        try:
            validated = spec.input_model.model_validate(args)
        except ValidationError as exc:
            return EditVerdict(ok=False, reason=f"invalid arguments: {_describe(exc)}")
        risk = spec.classify(validated) if spec.classify else spec.default_risk
        decision = self._policy.decide(tool=tool, risk=risk, principal=principal, tainted=False)
        if decision is PolicyDecision.DENY:
            return EditVerdict(ok=False, reason="policy does not allow the edited call", risk=risk)
        return EditVerdict(ok=True, risk=risk, normalized_args=_normalized(validated))

    async def execute(
        self,
        invocation: ToolInvocation,
        *,
        emit: EventEmit = _noop_emit,
        admit: Admit | None = None,
    ) -> ToolResult:
        """Run one invocation through the full pipeline. Never raises for tool-level failures.

        `admit` charges a run-level budget. It returns a refusal message, or None to proceed.
        Cancellation propagates, and leaves any claimed approval and STARTED execution as they
        are: the outcome is unknown, and startup reconciliation reports it honestly.
        """
        started = time.monotonic()
        with bind(run_id=invocation.run_id, tool_call_id=invocation.tool_call_id):
            gate = await self._gate(invocation, started, admit, emit)
            if isinstance(gate, ToolResult):
                return gate
            return await self._execute_gated(invocation, gate, started, emit)

    async def _execute_gated(
        self, inv: ToolInvocation, gate: _Gate, started: float, emit: EventEmit
    ) -> ToolResult:
        spec, risk = gate.spec, gate.risk
        ctx = ToolContext(
            run_id=inv.run_id,
            agent=inv.agent,
            principal=inv.principal,
            tool_call_id=inv.tool_call_id,
            emit=emit,
        )

        def fail(status: ToolStatus, code: str, message: str) -> Awaitable[ToolResult]:
            return self._fail(
                inv, spec, started, status=status, code=code, message=message, risk=risk
            )

        execution_id: str | None = None
        if spec.idempotency is not ToolIdempotency.READ_ONLY and not spec.orchestration:
            started_id = await self._start_execution(inv, gate)
            if started_id is None:
                result = await fail(
                    "error",
                    "execution_not_recorded",
                    f"'{spec.name}' was not run: its start could not be recorded, or this exact "
                    "call already ran. Do not retry; tell the user.",
                )
                await self._settle(None, gate.claim, ok=False)  # nothing ran
                return result
            execution_id = started_id

        ok = False
        try:
            try:
                raw = await self._run(spec, gate.args, ctx)
            except TimeoutError:
                result = await fail(
                    "error", "timeout", f"'{spec.name}' timed out after {spec.timeout_s:g}s."
                )
            except ToolError as exc:
                result = await fail("error", exc.code, redact_text(str(exc))[:_ERROR_CHARS])
            except Exception:
                log.exception("tool %s crashed", spec.name)  # details stay in the log
                result = await fail(
                    "error", "internal_error", f"'{spec.name}' failed unexpectedly."
                )
            else:
                ok = True
                try:
                    result = await self._succeed(inv, spec, raw, risk, started)
                except Exception:
                    # The call ran; only reporting its result failed. Say so, never hide it.
                    log.exception("tool %s result could not be processed", spec.name)
                    result = await fail(
                        "error",
                        "result_unavailable",
                        f"'{spec.name}' ran but its result could not be read. "
                        "Do not repeat it; tell the user.",
                    )
        except asyncio.CancelledError:
            raise  # outcome unknown: leave STARTED / CONSUMED for reconciliation
        await self._settle(execution_id, gate.claim, ok=ok)
        if ok:
            self._taint_from(inv, spec)
        return result

    async def _start_execution(self, inv: ToolInvocation, gate: _Gate) -> str | None:
        """Persist STARTED before a side effect runs (ARCHITECTURE 4.2 step 8). None: do not run."""
        if self._executions is None:
            log.error("side-effect tool %s has no execution log; refusing to run", gate.spec.name)
            return None
        normalized = _normalized(gate.args)
        ph = approval_payload_hash(
            gate.spec.name, gate.spec.version, inv.principal.value, normalized
        )
        execution_id = self._new_id("exe")
        try:
            recorded = await self._executions.start(
                execution_id=execution_id,
                run_id=inv.run_id,
                tool=gate.spec.name,
                agent=inv.agent,
                principal=inv.principal,
                risk=gate.risk,
                idempotency_key=approval_idempotency_key(inv.run_id, inv.tool_call_id, ph),
                args_hash=ph,
            )
        except Exception:
            log.exception("execution start failed for %s", gate.spec.name)
            return None
        return execution_id if recorded else None

    async def _settle(self, execution_id: str | None, claim: str | None, *, ok: bool) -> None:
        """Record the outcome. A failed write leaves STARTED/CONSUMED, which reconciliation owns."""
        try:
            if execution_id is not None and self._executions is not None:
                await self._executions.finish(execution_id, ok=ok)
            if claim is not None and self._broker is not None:
                await self._broker.mark_executed(claim, success=ok)
        except Exception:
            log.exception("could not record the outcome of %s", claim or execution_id)

    def _taint_from(self, inv: ToolInvocation, spec: ToolSpec) -> None:
        source = spec.taint_source or (TaintSource.TOOL_OUTPUT if spec.untrusted_output else None)
        if source is not None:
            self._taint.mark(inv.run_id, source)

    # ---- gate: stages 1-7 ------------------------------------------------------------------
    async def _gate(
        self, inv: ToolInvocation, started: float, admit: Admit | None, emit: EventEmit
    ) -> _Gate | ToolResult:
        spec = self._registry.get(inv.tool)
        refusal = self._unpermitted(inv, spec)
        if spec is None or refusal is not None:
            code, message = refusal or ("unknown_tool", f"There is no tool named '{inv.tool}'.")
            return await self._fail(inv, spec, started, status="denied", code=code, message=message)
        call = _Call(inv, spec, started, emit)
        if inv.approval_id is not None:
            # The budget was charged when the call was first proposed; resuming is not a new call.
            return await self._gate_approved(call)
        return await self._gate_new(call, admit)

    def _unpermitted(self, inv: ToolInvocation, spec: ToolSpec | None) -> tuple[str, str] | None:
        if spec is None:
            return "unknown_tool", f"There is no tool named '{inv.tool}'."
        if inv.tool not in self.granted(inv.agent):
            return "not_permitted", f"Tool '{spec.name}' is not available to this agent."
        return None

    async def _deny(
        self,
        call: _Call,
        code: str,
        message: str,
        *,
        risk: RiskClass | None = None,
        status: ToolStatus = "denied",
    ) -> ToolResult:
        return await self._fail(
            call.inv, call.spec, call.started, status=status, code=code, message=message, risk=risk
        )

    async def _gate_new(self, call: _Call, admit: Admit | None) -> _Gate | ToolResult:
        """A fresh call: budget, validation, risk, then policy."""
        refusal = admit() if admit is not None else None
        if refusal is not None:
            return await self._deny(call, "budget_exhausted", refusal)
        validated = self._validate(call.spec, call.inv.args)
        if isinstance(validated, str):
            return await self._deny(
                call,
                "invalid_arguments",
                f"Invalid arguments: {validated} Fix them and try again.",
                status="error",
            )
        args, risk = validated
        prior = await self._prior_approval(call, args)
        if prior is not None:
            # LangGraph re-runs the tool node on resume. Policy must not be asked again: the
            # run's taint is gone from memory, so it could now say ALLOW for a call that is
            # waiting on the user. Stay on the approval that already exists.
            return await self._request_approval(call, args, risk, prior.tainted)
        tainted = self._taint.is_tainted(call.inv.run_id)
        decision = self._policy.decide(
            tool=call.spec.name, risk=risk, principal=call.inv.principal, tainted=tainted
        )
        if decision is PolicyDecision.ALLOW:
            return _Gate(call.spec, args, risk)
        if decision is PolicyDecision.REQUIRE_APPROVAL:
            return await self._request_approval(call, args, risk, tainted)
        return await self._deny(
            call,
            "policy_denied",
            f"Policy does not allow '{call.spec.name}' (risk {risk.value}) right now. "
            "Nothing was executed. Tell the user instead of retrying.",
            risk=risk,
        )

    async def _prior_approval(self, call: _Call, args: BaseModel) -> ApprovalRecord | None:
        if self._broker is None:
            return None
        return await self._broker.existing(
            tool=call.spec.name,
            tool_version=call.spec.version,
            principal=call.inv.principal,
            args=_normalized(args),
            run_id=call.inv.run_id,
            tool_call_id=call.inv.tool_call_id,
        )

    @staticmethod
    def _validate(spec: ToolSpec, raw: dict[str, Any]) -> tuple[BaseModel, RiskClass] | str:
        """Validate arguments and classify risk from them. A string is the error message."""
        try:
            args = spec.input_model.model_validate(raw)
        except ValidationError as exc:
            return _describe(exc)
        return args, spec.classify(args) if spec.classify else spec.default_risk

    async def _request_approval(
        self, call: _Call, args: BaseModel, risk: RiskClass, tainted: bool
    ) -> ToolResult:
        """Create (or find) the approval and return a pending_approval ToolResult."""
        inv, spec = call.inv, call.spec
        if not inv.can_pause:
            return await self._deny(
                call,
                "approval_unavailable",
                f"'{spec.name}' needs the user's approval, which cannot be requested from here. "
                "Nothing was executed. Report that this action needs approval.",
                risk=risk,
            )
        if self._broker is None:  # fail closed: no broker means no approval path
            return await self._deny(
                call,
                "policy_denied",
                f"Policy requires approval for '{spec.name}' (risk {risk.value}), "
                "but no approval broker is configured. Nothing was executed.",
                risk=risk,
            )
        try:
            ticket = await self._broker.request(
                tool=spec.name,
                tool_version=spec.version,
                principal=inv.principal,
                risk=risk,
                args=_normalized(args),
                run_id=inv.run_id,
                tool_call_id=inv.tool_call_id,
                tainted=tainted,
            )
        except Exception:
            log.exception("approval broker request failed for %s", spec.name)
            return await self._deny(
                call,
                "approval_broker_error",
                f"Could not create approval request for '{spec.name}'. Nothing was executed.",
                risk=risk,
            )
        view = ticket.view
        if ticket.created:  # a replay (resume re-runs the node) must not re-announce it
            call.emit(events.ApprovalRequested(run_id=inv.run_id, approval=view))
            await self._record_audit(
                "tool.approval_requested",
                inv,
                {"risk": risk.value, "approval_id": view.approval_id, "tainted": tainted},
            )
        return ToolResult(
            tool_call_id=inv.tool_call_id,
            tool=inv.tool,
            status="pending_approval",
            content=(
                f"[approval_required:{view.approval_id}] Waiting for user to approve '{spec.name}'."
            ),
            preview=f"pending approval for {spec.name}",
            error_code="approval_required",
            duration_ms=_elapsed_ms(call.started),
            approval_id=view.approval_id,
        )

    async def _gate_approved(self, call: _Call) -> _Gate | ToolResult:
        """Decide, on the engine's own authority, whether an approved call may run.

        The caller's arguments are not trusted: an edited approval runs the stored edited
        payload, and an unedited one must hash to exactly what the user saw.
        """
        record = await self._load_approval(call)
        if isinstance(record, ToolResult):
            return record
        verified = await self._verify_approved(call, record)
        if isinstance(verified, ToolResult):
            return verified
        args, risk = verified
        return await self._claim(call, record, args, risk)

    async def _load_approval(self, call: _Call) -> ApprovalRecord | ToolResult:
        inv, spec = call.inv, call.spec
        if self._broker is None or inv.approval_id is None:
            return await self._deny(
                call, "policy_denied", "No approval broker available to verify this call."
            )
        record = await self._broker.get(inv.approval_id)
        if record is None:
            return await self._deny(call, "approval_not_found", "Approval record was not found.")
        if (
            record.tool != spec.name
            or record.principal is not inv.principal
            or record.run_id != inv.run_id
        ):
            return await self._deny(
                call, "approval_mismatch", "This approval belongs to a different call."
            )
        if record.state not in ("APPROVED", "APPROVED_EDITED"):
            code, message = _unusable(record)
            return await self._deny(call, code, message)
        if record.tainted:  # a resumed run keeps the taint it had when the approval was made
            self._taint.mark(inv.run_id, TaintSource.TOOL_OUTPUT)
        return record

    async def _verify_approved(
        self, call: _Call, record: ApprovalRecord
    ) -> tuple[BaseModel, RiskClass] | ToolResult:
        """Re-validate, re-classify, and re-hash. Any drift invalidates the approval."""
        spec, inv = call.spec, call.inv
        if record.tool_version != spec.version:
            return await self._invalidate(call, record, "tool_version_changed")
        raw = json.loads(record.args_json) if record.state == "APPROVED_EDITED" else inv.args
        validated = self._validate(spec, raw)
        if isinstance(validated, str):
            return await self._invalidate(call, record, "invalid_arguments")
        args, risk = validated
        live_hash = approval_payload_hash(
            spec.name, spec.version, inv.principal.value, _normalized(args)
        )
        if live_hash != record.payload_hash:
            return await self._invalidate(call, record, "payload_mismatch")
        if risk.rank > record.risk.rank:
            return await self._invalidate(call, record, "risk_raised")
        return args, risk

    async def _invalidate(self, call: _Call, record: ApprovalRecord, reason: str) -> ToolResult:
        assert self._broker is not None
        await self._broker.invalidate(record.id, reason=reason)
        call.emit(
            events.ApprovalResolved(
                run_id=call.inv.run_id, approval_id=record.id, outcome="invalidated"
            )
        )
        return await self._deny(
            call,
            "approval_invalidated",
            f"The approved call no longer matches what the user approved ({reason}). "
            "Execution denied; nothing was executed.",
            risk=record.risk,
        )

    async def _claim(
        self, call: _Call, record: ApprovalRecord, args: BaseModel, risk: RiskClass
    ) -> _Gate | ToolResult:
        """Re-check policy (it may have tightened since), then claim the approval atomically."""
        assert self._broker is not None
        decision = self._policy.decide(
            tool=call.spec.name,
            risk=risk,
            principal=call.inv.principal,
            tainted=record.tainted or self._taint.is_tainted(call.inv.run_id),
        )
        if decision is PolicyDecision.DENY:
            await self._broker.invalidate(record.id, reason="policy_denied")
            return await self._deny(
                call, "policy_denied", "Policy no longer allows this call.", risk=risk
            )
        if not await self._broker.consume(record.id):
            code, message = _unusable(await self._broker.get(record.id) or record)
            return await self._deny(call, code, message, risk=risk)
        return _Gate(call.spec, args, risk, claim=record.id)

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

    async def _record_audit(
        self, event_type: str, inv: ToolInvocation, data: dict[str, Any]
    ) -> None:
        try:
            await self._audit.append(
                event_type=event_type,
                actor=inv.principal.value,
                subject_type="tool",
                subject_id=inv.tool,
                data={"tool_call_id": inv.tool_call_id, **data},
            )
        except Exception:
            log.exception("audit write failed for %s", inv.tool)

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
        status_key = result.status if result.status in ("ok", "denied", "error") else "denied"
        event_type = {"ok": "tool.executed", "denied": "tool.denied", "error": "tool.failed"}[
            status_key
        ]
        canonical = json.dumps(inv.args, sort_keys=True, default=str, separators=(",", ":"))
        data: dict[str, Any] = {
            "tool_call_id": inv.tool_call_id,
            "approval_id": inv.approval_id,
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


def _unusable(record: ApprovalRecord) -> tuple[str, str]:
    """(error code, message) for an approval that cannot be used to run the call."""
    state = record.state
    if state in ("CONSUMED", "EXECUTED", "EXEC_FAILED"):
        return (
            "approval_already_used",
            "This approved action was already started, so its result may not be known. "
            "Do not repeat it; tell the user.",
        )
    reasons = {
        # Still approved yet unclaimable: the only way is that its TTL passed.
        "APPROVED": ("approval_expired", "The approval expired. Ask again if it is still needed."),
        "APPROVED_EDITED": (
            "approval_expired",
            "The approval expired. Ask again if it is still needed.",
        ),
        "PENDING": ("approval_pending", "The user has not decided yet."),
        "REJECTED": ("approval_rejected", "The user rejected this action. Nothing was executed."),
        "EXPIRED": ("approval_expired", "The approval expired. Ask again if it is still needed."),
        "CANCELLED": ("approval_cancelled", "The approval was cancelled. Nothing was executed."),
        "INVALIDATED": (
            "approval_invalidated",
            "The approval was invalidated. Nothing was executed.",
        ),
    }
    return reasons.get(state, ("approval_unusable", f"The approval is {state.lower()}."))


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
