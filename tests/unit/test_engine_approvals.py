"""Tests for ToolExecutionEngine approval flow and gate conditions (docs/phases/05-approvals.md)."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from opsagent.application.approval_broker import ApprovalBroker
from opsagent.domain import (
    RiskClass,
    ToolInvocation,
    events,
)
from opsagent.tools import (
    ToolContext,
    ToolExecutionEngine,
    ToolRegistry,
    ToolSpec,
)
from opsagent.tools.policy import PolicyConfig, PolicyEngine
from tests.support.fakes import MemoryApprovalRepo, MemoryArtifacts, MemoryAudit


class SendMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(min_length=1)
    body: str = Field(min_length=1)


class EngineApprovalFixture:
    def __init__(self, *, tainted: bool = False) -> None:
        self.audit = MemoryAudit()
        self.artifacts = MemoryArtifacts()
        self.repo = MemoryApprovalRepo()
        self.broker = ApprovalBroker(repo=self.repo, audit=self.audit, new_id=self._new_id)
        self.policy = PolicyEngine(PolicyConfig())
        self.sent_messages: list[dict[str, Any]] = []
        self._seq = 0

        async def send_handler(args: SendMessageIn, ctx: ToolContext) -> dict[str, str]:
            self.sent_messages.append({"recipient": args.recipient, "body": args.body})
            return {"status": "sent", "message_id": f"msg_{len(self.sent_messages)}"}

        self.spec = ToolSpec(
            name="mail.send_message",
            version="1",
            summary="Send email message",
            input_model=SendMessageIn,
            handler=send_handler,
            default_risk=RiskClass.R3,
        )
        self.registry = ToolRegistry([self.spec])
        self.engine = ToolExecutionEngine(
            registry=self.registry,
            grants={"main": frozenset({"mail.send_message"})},
            policy=self.policy,
            audit=self.audit,
            artifacts=self.artifacts,
            broker=self.broker,
            tainted=tainted,
        )

    def _new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq:04d}"


async def test_require_approval_emits_event_and_pauses() -> None:
    env = EngineApprovalFixture()
    emitted: list[Any] = []
    inv = ToolInvocation(
        tool="mail.send_message",
        args={"recipient": "boss@example.com", "body": "Weekly Report"},
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
    )
    result = await env.engine.execute(inv, emit=emitted.append)
    assert result.status == "pending_approval"
    assert result.approval_id == "apr_0001"
    assert len(emitted) == 1
    assert isinstance(emitted[0], events.ApprovalRequested)
    assert emitted[0].approval.approval_id == "apr_0001"
    assert emitted[0].approval.risk == RiskClass.R3
    # Tool handler must NOT have executed
    assert len(env.sent_messages) == 0


async def test_mutate_payload_after_approval_prevents_execution() -> None:
    """GATE: Mutate payload after approval => no execution."""
    env = EngineApprovalFixture()
    inv = ToolInvocation(
        tool="mail.send_message",
        args={"recipient": "boss@example.com", "body": "Weekly Report"},
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
    )
    result = await env.engine.execute(inv)
    approval_id = result.approval_id
    assert approval_id is not None

    # User approved original payload
    await env.broker.approve(approval_id)

    # Attacker / rogueTurn tries to execute with MUTATED payload
    mutated_inv = ToolInvocation(
        tool="mail.send_message",
        args={"recipient": "attacker@evil.com", "body": "Weekly Report"},  # mutated recipient!
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
        approval_id=approval_id,
    )
    exec_result = await env.engine.execute(mutated_inv)
    assert exec_result.status == "denied"
    assert exec_result.error_code == "approval_invalidated"

    # Crucial assertion: tool handler NEVER ran!
    assert len(env.sent_messages) == 0

    # Approval in DB was marked INVALIDATED
    rec = await env.repo.get(approval_id)
    assert rec is not None and rec.state == "INVALIDATED"


async def test_double_click_race_executes_only_once() -> None:
    """GATE: Double-click / race => one execution."""
    env = EngineApprovalFixture()
    args = {"recipient": "boss@example.com", "body": "Report"}
    inv = ToolInvocation(
        tool="mail.send_message",
        args=args,
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
    )
    res = await env.engine.execute(inv)
    approval_id = res.approval_id
    assert approval_id is not None
    await env.broker.approve(approval_id)

    approved_inv1 = ToolInvocation(
        tool="mail.send_message",
        args=args,
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
        approval_id=approval_id,
    )
    approved_inv2 = ToolInvocation(
        tool="mail.send_message",
        args=args,
        agent="main",
        tool_call_id="call_1",
        run_id="run_1",
        approval_id=approval_id,
    )

    # Run concurrently (simulating double-click or race condition)
    res1, res2 = await asyncio.gather(
        env.engine.execute(approved_inv1),
        env.engine.execute(approved_inv2),
    )

    statuses = {res1.status, res2.status}
    assert "ok" in statuses
    assert "denied" in statuses

    # Exactly one executed successfully!
    assert len(env.sent_messages) == 1

    rec = await env.repo.get(approval_id)
    assert rec is not None and rec.state == "EXECUTED"


async def test_tainted_run_cannot_send_without_approval() -> None:
    """GATE: Tainted run cannot send to recipient without approval."""
    env = EngineApprovalFixture(tainted=True)
    inv = ToolInvocation(
        tool="mail.send_message",
        args={"recipient": "external@evil.com", "body": "Secret data"},
        agent="main",
        tool_call_id="call_1",
        run_id="run_tainted",
    )
    result = await env.engine.execute(inv)
    # Must hit approval gate
    assert result.status == "pending_approval"
    rec = await env.repo.get(result.approval_id or "")
    assert rec is not None
    assert rec.tainted is True
    assert len(env.sent_messages) == 0


async def test_fails_closed_when_no_broker_configured() -> None:
    env = EngineApprovalFixture()
    # Engine without broker
    no_broker_engine = ToolExecutionEngine(
        registry=env.registry,
        grants={"main": frozenset({"mail.send_message"})},
        policy=env.policy,
        audit=env.audit,
        artifacts=env.artifacts,
        broker=None,  # No broker!
    )
    inv = ToolInvocation(
        tool="mail.send_message",
        args={"recipient": "user@example.com", "body": "test"},
        agent="main",
        tool_call_id="c1",
    )
    result = await no_broker_engine.execute(inv)
    assert result.status == "denied"
    assert result.error_code == "policy_denied"
    assert len(env.sent_messages) == 0
