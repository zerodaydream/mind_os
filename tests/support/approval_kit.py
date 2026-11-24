"""A real broker, engine, and policy on in-memory ports, with a few approval-shaped tools."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opsagent.application.approval_broker import ApprovalBroker
from opsagent.domain import (
    AuthError,
    BoundState,
    Principal,
    Resolution,
    RiskClass,
    SessionMode,
    TaintLedger,
    ToolIdempotency,
    ToolInvocation,
    ToolResult,
)
from opsagent.tools import ToolContext, ToolExecutionEngine, ToolRegistry, ToolSpec
from opsagent.tools.policy import PolicyConfig, PolicyEngine
from tests.support.fakes import (
    ManualClock,
    MemoryApprovalRepo,
    MemoryArtifacts,
    MemoryAudit,
    MemoryExecutionLog,
)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)
    cc: str | None = None  # a default: normalization must make `{}` and `{cc: None}` the same


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)


class QueryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = "x"


def _edit_risk(args: EditIn) -> RiskClass:
    """Editing inside the workspace is R2; anywhere else is R3."""
    return RiskClass.R2 if args.path.startswith("/work/") else RiskClass.R3


class Env:
    """Everything a test needs to drive the approval flow end to end."""

    def __init__(
        self,
        *,
        config: PolicyConfig | None = None,
        mode: SessionMode | None = None,
        policy: Any = None,
        with_broker: bool = True,
    ) -> None:
        self.clock = ManualClock()
        self.repo = MemoryApprovalRepo(self.clock)
        self.audit = MemoryAudit()
        self.executions = MemoryExecutionLog()
        self.taint = TaintLedger()
        self._seq = 0
        self.sent: list[dict[str, Any]] = []
        self.edited: list[str] = []
        self.drafts: list[str] = []
        self.auth_error: AuthError | None = None  # what `mail.fetch` raises, if set
        self.fetches = 0
        self.disk: dict[str, str] = {}  # path -> content: the state `files.edit` binds approvals to
        self.events: list[Any] = []
        self.broker = ApprovalBroker(
            repo=self.repo, audit=self.audit, new_id=self.new_id, clock=self.clock
        )

        async def send(args: SendIn, ctx: ToolContext) -> dict[str, str]:
            self.sent.append({"recipient": args.recipient, "body": args.body, "cc": args.cc})
            return {"id": f"msg_{len(self.sent)}"}

        async def edit(args: EditIn, ctx: ToolContext) -> str:
            self.edited.append(args.path)
            return "edited"

        async def bind_edit(args: EditIn) -> BoundState:
            content = self.disk.get(args.path, "")
            return BoundState(
                resolved_targets={args.path: f"/real{args.path}"},
                base_state_hashes={args.path: hashlib.sha256(content.encode()).hexdigest()},
            )

        async def draft(args: SendIn, ctx: ToolContext) -> str:
            self.drafts.append(args.recipient)
            return "drafted"

        async def search(args: QueryIn, ctx: ToolContext) -> str:
            return f"results for {args.q}"

        async def fetch(args: QueryIn, ctx: ToolContext) -> str:
            self.fetches += 1
            if self.auth_error is not None:
                raise self.auth_error
            return "fetched"

        self.registry = ToolRegistry(
            [
                ToolSpec(
                    name="mail.send_message",
                    version="1",
                    summary="Send mail",
                    input_model=SendIn,
                    handler=send,
                    default_risk=RiskClass.R3,
                    idempotency=ToolIdempotency.NON_IDEMPOTENT,
                ),
                ToolSpec(
                    name="files.edit",
                    version="1",
                    summary="Edit a file",
                    input_model=EditIn,
                    handler=edit,
                    classify=_edit_risk,
                    bind_state=bind_edit,
                    idempotency=ToolIdempotency.NON_IDEMPOTENT,
                ),
                ToolSpec(
                    name="mail.create_draft",
                    version="1",
                    summary="Draft mail",
                    input_model=SendIn,
                    handler=draft,
                    default_risk=RiskClass.R1,
                    idempotency=ToolIdempotency.NON_IDEMPOTENT,
                ),
                ToolSpec(
                    name="mail.fetch",
                    version="1",
                    summary="Fetch mail",
                    input_model=QueryIn,
                    handler=fetch,
                ),
                ToolSpec(
                    name="mail.search",
                    version="1",
                    summary="Search mail",
                    input_model=QueryIn,
                    handler=search,
                    untrusted_output=True,
                ),
            ]
        )
        self.policy = policy or PolicyEngine(config or PolicyConfig(), mode)
        self.engine = ToolExecutionEngine(
            registry=self.registry,
            grants={"main": self.registry.names()},
            policy=self.policy,
            audit=self.audit,
            artifacts=MemoryArtifacts(),
            broker=self.broker if with_broker else None,
            taint=self.taint,
            executions=self.executions,
            new_id=self.new_id,
        )

    def new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:04d}"

    def invocation(
        self,
        tool: str = "mail.send_message",
        args: dict[str, Any] | None = None,
        *,
        call_id: str = "call_1",
        run_id: str = "run_1",
        approval_id: str | None = None,
        can_pause: bool = True,
    ) -> ToolInvocation:
        return ToolInvocation(
            tool=tool,
            args=args if args is not None else {"recipient": "boss@example.com", "body": "Hi"},
            agent="main",
            tool_call_id=call_id,
            run_id=run_id,
            principal=Principal.AGENT,
            approval_id=approval_id,
            can_pause=can_pause,
        )

    async def run(self, inv: ToolInvocation) -> ToolResult:
        return await self.engine.execute(inv, emit=self.events.append)

    async def propose(self, *a: Any, **kw: Any) -> ToolResult:
        return await self.run(self.invocation(*a, **kw))

    async def approve(self, approval_id: str) -> Resolution:
        return await self.broker.resolve(approval_id, decision="approve")

    async def run_approved(self, approval_id: str, *a: Any, **kw: Any) -> ToolResult:
        return await self.run(self.invocation(*a, approval_id=approval_id, **kw))

    def state(self, approval_id: str) -> str:
        return self.repo.records[approval_id].state
