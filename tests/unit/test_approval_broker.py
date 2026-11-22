"""Tests for ApprovalBroker: lifecycle, hashing, state transitions, and audit trail."""

from __future__ import annotations

import pytest

from opsagent.application.approval_broker import ApprovalBroker
from opsagent.domain import Principal, RiskClass, approval_payload_hash
from tests.support.fakes import MemoryApprovalRepo, MemoryAudit


@pytest.fixture
def broker_env() -> tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit]:
    repo = MemoryApprovalRepo()
    audit = MemoryAudit()
    seq = 0

    def new_id(prefix: str) -> str:
        nonlocal seq
        seq += 1
        return f"{prefix}_{seq:04d}"

    broker = ApprovalBroker(repo=repo, audit=audit, new_id=new_id)
    return broker, repo, audit


async def test_request_creates_pending_approval(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, audit = broker_env
    args = {"to": "user@example.com", "subject": "Hello"}
    view = await broker.request(
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args=args,
        run_id="run_100",
        tool_call_id="call_200",
        tainted=True,
    )
    assert view.approval_id == "apr_0001"
    assert view.tool == "mail.send_message"
    assert view.risk == RiskClass.R3
    assert view.tainted is True
    assert view.payload_hash == approval_payload_hash(
        "mail.send_message", "1", "AGENT", args
    )

    rec = await repo.get("apr_0001")
    assert rec is not None
    assert rec.state == "PENDING"
    assert rec.run_id == "run_100"
    assert rec.tainted is True

    assert audit.types() == ["approval.created"]
    assert audit.rows[0]["data"]["payload_hash"] == view.payload_hash


async def test_approve_transitions_to_approved(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, audit = broker_env
    view = await broker.request(
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={"text": "hello"},
        run_id="run_1",
        tool_call_id="c1",
    )
    ok = await broker.approve(view.approval_id, actor="USER")
    assert ok is True
    rec = await repo.get(view.approval_id)
    assert rec is not None and rec.state == "APPROVED"
    assert "approval.approved" in audit.types()


async def test_approve_edited_updates_hash_and_args(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, audit = broker_env
    view = await broker.request(
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={"text": "draft"},
        run_id="run_1",
        tool_call_id="c1",
    )
    new_args = {"text": "edited message"}
    new_hash = await broker.approve_edited(
        view.approval_id,
        new_args=new_args,
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
    )
    assert new_hash is not None
    assert new_hash != view.payload_hash
    assert new_hash == approval_payload_hash("mail.send_message", "1", "AGENT", new_args)

    rec = await repo.get(view.approval_id)
    assert rec is not None
    assert rec.state == "APPROVED_EDITED"
    assert rec.payload_hash == new_hash
    assert "approval.edited" in audit.types()


async def test_reject_records_reason(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, audit = broker_env
    view = await broker.request(
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={"text": "hello"},
        run_id="run_1",
        tool_call_id="c1",
    )
    ok = await broker.reject(view.approval_id, reason="Wrong recipient")
    assert ok is True
    rec = await repo.get(view.approval_id)
    assert rec is not None and rec.state == "REJECTED"
    assert "approval.rejected" in audit.types()
    assert audit.rows[-1]["data"]["reason"] == "Wrong recipient"


async def test_consume_claims_approved_row_once(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, _audit = broker_env
    view = await broker.request(
        tool="test.action",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={},
        run_id="run_1",
        tool_call_id="c1",
    )
    # Cannot consume while PENDING
    assert await broker.consume(view.approval_id) is False

    await broker.approve(view.approval_id)
    # First consume claims the approval
    assert await broker.consume(view.approval_id) is True
    rec = await repo.get(view.approval_id)
    assert rec is not None and rec.state == "CONSUMED"

    # Second consume must fail (double execution prevention)
    assert await broker.consume(view.approval_id) is False


async def test_mark_executed_and_invalidate(
    broker_env: tuple[ApprovalBroker, MemoryApprovalRepo, MemoryAudit],
) -> None:
    broker, repo, audit = broker_env
    view = await broker.request(
        tool="test.action",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={},
        run_id="run_1",
        tool_call_id="c1",
    )
    await broker.approve(view.approval_id)
    await broker.consume(view.approval_id)
    await broker.mark_executed(view.approval_id, success=True)
    rec = await repo.get(view.approval_id)
    assert rec is not None and rec.state == "EXECUTED"

    # Test invalidation path
    view2 = await broker.request(
        tool="test.action",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args={},
        run_id="run_2",
        tool_call_id="c2",
    )
    await broker.approve(view2.approval_id)
    await broker.consume(view2.approval_id)
    await broker.invalidate(view2.approval_id)
    rec2 = await repo.get(view2.approval_id)
    assert rec2 is not None and rec2.state == "INVALIDATED"
    assert "approval.invalidated" in audit.types()
