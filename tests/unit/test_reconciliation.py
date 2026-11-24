"""Startup reconciliation (ARCHITECTURE 11.4): storage repairs, all audited, nothing invented."""

from __future__ import annotations

from datetime import timedelta

from opsagent.application.reconciliation import reconcile_storage
from opsagent.domain import Principal, RiskClass
from tests.support.approval_kit import Env


async def started(env: Env, key: str, run_id: str = "run_1") -> str:
    execution_id = env.new_id("exe")
    await env.executions.start(
        execution_id=execution_id,
        run_id=run_id,
        tool="mail.send_message",
        agent="main",
        principal=Principal.AGENT,
        risk=RiskClass.R3,
        idempotency_key=key,
        args_hash="h",
    )
    return execution_id


async def test_a_clean_database_reports_clean() -> None:
    env = Env()
    report = await reconcile_storage(broker=env.broker, executions=env.executions, audit=env.audit)
    assert report.clean and env.audit.rows == []


async def test_in_flight_side_effects_become_unknown_never_success_or_failure() -> None:
    env = Env()
    in_flight = await started(env, "k1")
    done = await started(env, "k2")
    await env.executions.finish(done, ok=True)

    report = await reconcile_storage(broker=env.broker, executions=env.executions, audit=env.audit)

    assert [e[0] for e in report.unknown_executions] == [in_flight]
    assert env.executions.rows[in_flight]["state"] == "UNKNOWN"
    assert env.executions.rows[done]["state"] == "FINISHED"
    audited = [r for r in env.audit.rows if r["event_type"] == "tool.outcome_unknown"]
    assert [r["subject_id"] for r in audited] == [in_flight]
    assert audited[0]["data"]["tool"] == "mail.send_message"


async def test_stale_approvals_expire_and_consumed_ones_are_settled_as_unknown() -> None:
    env = Env()
    stale = (await env.propose(call_id="c1")).approval_id or ""
    stuck = (await env.propose(call_id="c2", args={"recipient": "b@c.d", "body": "B"})).approval_id
    assert stuck is not None
    await env.approve(stuck)
    await env.broker.consume(stuck)  # the process died mid-execution
    env.clock.advance(timedelta(hours=3))

    report = await reconcile_storage(broker=env.broker, executions=env.executions, audit=env.audit)

    assert [r.id for r in report.expired] == [stale]
    assert [r.id for r in report.unknown_approvals] == [stuck]
    assert env.state(stale) == "EXPIRED" and env.state(stuck) == "EXEC_FAILED"
    assert {"approval.expired", "approval.outcome_unknown"} <= set(env.audit.types())
    again = await reconcile_storage(broker=env.broker, executions=env.executions, audit=env.audit)
    assert again.clean  # idempotent: the next start repairs nothing twice
