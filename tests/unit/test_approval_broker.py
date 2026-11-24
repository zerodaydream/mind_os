"""ApprovalBroker: creation, decisions, expiry, cancellation, and the audit trail."""

from __future__ import annotations

from datetime import timedelta

import pytest

from opsagent.application.approval_broker import ApprovalBroker
from opsagent.domain import Principal, RiskClass, approval_payload_hash, bind
from tests.support.approval_kit import Env

ARGS = {"recipient": "user@example.com", "body": "Hello", "cc": None}


async def request(env: Env, **kw: object) -> str:
    ticket = await env.broker.request(
        **{
            "tool": "mail.send_message",
            "tool_version": "1",
            "principal": Principal.AGENT,
            "risk": RiskClass.R3,
            "args": ARGS,
            "run_id": "run_100",
            "tool_call_id": "call_200",
            "tainted": True,
            **kw,
        }  # type: ignore[arg-type]
    )
    return ticket.view.approval_id


async def test_request_creates_a_pending_approval_with_the_resume_context() -> None:
    env = Env()
    with bind(run_id="run_100", conversation_id="conv_1", thread_id="thr_1"):
        ticket = await env.broker.request(
            tool="mail.send_message",
            tool_version="1",
            principal=Principal.AGENT,
            risk=RiskClass.R3,
            args=ARGS,
            run_id="run_100",
            tool_call_id="call_200",
            tainted=True,
        )
    assert ticket.created and ticket.view.tainted
    assert ticket.view.payload_hash == approval_payload_hash(
        "mail.send_message", "1", "AGENT", ARGS
    )
    rec = env.repo.records[ticket.view.approval_id]
    assert (rec.state, rec.thread_id, rec.conversation_id, rec.tool_call_id) == (
        "PENDING",
        "thr_1",
        "conv_1",
        "call_200",
    )
    assert rec.expires_at is not None
    assert env.audit.types() == ["approval.created"]


async def test_request_is_idempotent_per_call_and_payload() -> None:
    env = Env()
    first = await request(env)
    again = await env.broker.request(
        tool="mail.send_message",
        tool_version="1",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        args=ARGS,
        run_id="run_100",
        tool_call_id="call_200",
        tainted=True,
    )
    assert again.view.approval_id == first and not again.created
    assert len(env.repo.records) == 1 and env.audit.types() == ["approval.created"]
    # a different payload for the same call id is a different approval
    other = await request(env, args={**ARGS, "body": "Other"})
    assert other != first


async def test_decisions_apply_only_to_pending_and_are_audited_with_the_actor() -> None:
    env = Env()
    aid = await request(env)
    assert (await env.broker.resolve(aid, decision="approve")).outcome == "approved"
    again = await env.broker.resolve(aid, decision="approve")
    assert again.outcome == "unavailable" and "approved" in (again.detail or "")
    rejected = await env.broker.resolve(aid, decision="reject")
    assert rejected.outcome == "unavailable" and env.state(aid) == "APPROVED"
    assert (await env.broker.resolve("apr_missing", decision="approve")).outcome == "unavailable"
    assert (await env.broker.resolve(aid, decision="bogus")).outcome == "unavailable"
    approved = [r for r in env.audit.rows if r["event_type"] == "approval.approved"]
    assert len(approved) == 1 and approved[0]["actor"] == "USER"


async def test_reject_keeps_the_reason_in_the_audit_trail() -> None:
    env = Env()
    aid = await request(env)
    resolution = await env.broker.resolve(aid, decision="reject", comment="wrong recipient")
    assert resolution.outcome == "rejected" and resolution.detail == "wrong recipient"
    row = next(r for r in env.audit.rows if r["event_type"] == "approval.rejected")
    assert row["data"]["reason"] == "wrong recipient"


async def test_expiry_wins_over_a_late_decision_and_is_audited() -> None:
    env = Env()
    aid = await request(env)
    env.clock.advance(timedelta(hours=3))
    assert (await env.broker.resolve(aid, decision="approve")).outcome == "expired"
    assert env.state(aid) == "EXPIRED"
    assert env.audit.types().count("approval.expired") == 1
    # a second late click sees an already-expired approval, not a second transition
    assert (await env.broker.resolve(aid, decision="reject")).outcome == "expired"
    assert env.audit.types().count("approval.expired") == 1


async def test_edit_without_a_validator_is_refused_not_trusted() -> None:
    env = Env()
    bare = ApprovalBroker(repo=env.repo, audit=env.audit, new_id=env.new_id, clock=env.clock)
    aid = await request(env)
    result = await bare.resolve(
        aid, decision="edit", edited_args={"recipient": "x@y.z", "body": "b"}
    )
    assert result.outcome == "refused" and env.state(aid) == "PENDING"


async def test_the_validator_binds_once() -> None:
    env = Env()
    with pytest.raises(RuntimeError, match="already bound"):
        env.broker.bind_validator(env.engine)  # the engine bound itself at construction


async def test_consume_is_single_use_and_mark_executed_only_follows_consume() -> None:
    env = Env()
    aid = await request(env)
    assert await env.broker.consume(aid) is False  # not approved yet
    await env.approve(aid)
    assert await env.broker.consume(aid) is True
    assert await env.broker.consume(aid) is False
    await env.broker.mark_executed(aid, success=True)
    await env.broker.mark_executed(aid, success=False)  # a late, wrong write changes nothing
    assert env.state(aid) == "EXECUTED"
    assert env.audit.types().count("approval.executed") == 1


async def test_invalidate_records_why() -> None:
    env = Env()
    aid = await request(env)
    await env.approve(aid)
    await env.broker.invalidate(aid, reason="payload_mismatch")
    row = next(r for r in env.audit.rows if r["event_type"] == "approval.invalidated")
    assert env.state(aid) == "INVALIDATED" and row["data"]["reason"] == "payload_mismatch"


async def test_cancelling_a_run_cancels_pending_approvals_and_stops_redelivery() -> None:
    env = Env()
    with bind(thread_id="thr_1", conversation_id="conv_1"):
        pending = await request(env, tool_call_id="c1")
        granted = await request(env, tool_call_id="c2", args={**ARGS, "body": "B"})
        other = await request(
            env, tool_call_id="c3", run_id="run_other", args={**ARGS, "body": "C"}
        )
    await env.approve(granted)
    assert await env.broker.cancel_for_run("run_100") == 1
    assert env.state(pending) == "CANCELLED" and env.state(granted) == "APPROVED"
    left = {r.id for r in await env.broker.undelivered()}
    assert left == {other}  # neither cancelled nor approved-but-abandoned rows come back


async def test_sweep_expires_stale_rows_with_audit_and_reconciler_settles_consumed() -> None:
    env = Env()
    stale = await request(env, tool_call_id="c1")
    stuck = await request(env, tool_call_id="c2", args={**ARGS, "body": "B"})
    await env.approve(stuck)
    await env.broker.consume(stuck)
    env.clock.advance(timedelta(hours=3))
    assert [r.id for r in await env.broker.expire_stale()] == [stale]
    assert [r.id for r in await env.broker.abandon_consumed()] == [stuck]
    assert env.state(stuck) == "EXEC_FAILED" and env.repo.records[stuck].actor == "RECONCILER"
    assert "approval.outcome_unknown" in env.audit.types()
