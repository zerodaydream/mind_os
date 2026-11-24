"""The engine's approval gate (ARCHITECTURE 5.4, docs/phases/05-approvals.md gate)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from mindos.domain import (
    ApprovalMode,
    PolicyDecision,
    Principal,
    ReauthRequired,
    RiskClass,
    ScopeRequired,
    SessionMode,
    TaintSource,
    TransientAuthError,
    events,
)
from mindos.tools.policy import PolicyConfig, PolicyEngine, ToolPolicy
from tests.support.approval_kit import Env


def only_approval(env: Env) -> str:
    assert len(env.repo.records) == 1
    return next(iter(env.repo.records))


# ---- pausing ---------------------------------------------------------------------------------
async def test_require_approval_pauses_emits_once_and_audits() -> None:
    env = Env()
    result = await env.propose()
    assert result.status == "pending_approval" and result.approval_id is not None
    assert env.sent == []
    requested = [e for e in env.events if isinstance(e, events.ApprovalRequested)]
    assert len(requested) == 1 and requested[0].approval.approval_id == result.approval_id
    assert env.state(result.approval_id) == "PENDING"
    assert "tool.approval_requested" in env.audit.types()


async def test_replaying_the_call_returns_the_same_approval_without_announcing_it_again() -> None:
    """LangGraph re-runs the tool node on resume; that must not create a second approval."""
    env = Env()
    first = await env.propose()
    again = await env.propose()
    assert again.approval_id == first.approval_id
    assert len(env.repo.records) == 1
    assert len([e for e in env.events if isinstance(e, events.ApprovalRequested)]) == 1
    assert env.audit.types().count("approval.created") == 1


async def test_a_replay_stays_on_its_approval_even_after_taint_is_forgotten() -> None:
    """Taint lives in memory. After a resume it is gone, so policy alone would say ALLOW."""
    env = Env()
    await env.propose("mail.search", {"q": "inbox"})  # taints run_1
    draft_args = {"recipient": "a@example.com", "body": "x"}
    first = await env.propose("mail.create_draft", draft_args)
    assert first.status == "pending_approval"  # R1 would auto-run if the run were not tainted
    env.engine.end_run("run_1")  # what a resume looks like to the engine
    again = await env.propose("mail.create_draft", draft_args)
    assert again.approval_id == first.approval_id and env.drafts == []


async def test_fails_closed_without_a_broker() -> None:
    bare = Env(with_broker=False)
    result = await bare.propose()
    assert result.status == "denied" and result.error_code == "policy_denied"
    assert bare.sent == []


async def test_an_agent_that_cannot_pause_gets_a_deferred_approval_and_does_not_run() -> None:
    env = Env()
    result = await env.propose(can_pause=False)
    assert result.status == "pending_approval" and "approval_queued" in result.content
    record = env.repo.records[result.approval_id or ""]
    assert (record.deferred, record.agent, record.state) == (True, "main", "PENDING")
    assert env.sent == []
    requested = [e for e in env.events if isinstance(e, events.ApprovalRequested)]
    assert [e.approval.deferred for e in requested] == [True]


async def test_a_deferred_approval_runs_once_from_the_stored_row_and_never_twice() -> None:
    env = Env()
    pending = await env.propose(can_pause=False)
    aid = pending.approval_id or ""
    await env.approve(aid)
    record = env.repo.records[aid]
    first = await env.engine.run_approved(record, env.events.append)
    again = await env.engine.run_approved(env.repo.records[aid], env.events.append)
    assert first.status == "ok" and len(env.sent) == 1
    assert again.error_code == "approval_already_used" and len(env.sent) == 1
    assert env.state(aid) == "EXECUTED"


async def test_a_deferred_edit_runs_the_edited_arguments() -> None:
    env = Env()
    aid = (await env.propose(can_pause=False)).approval_id or ""
    edited = {"recipient": "boss@example.com", "body": "Edited"}
    await env.broker.resolve(aid, decision="edit", edited_args=edited)
    await env.engine.run_approved(env.repo.records[aid], env.events.append)
    assert env.sent[0]["body"] == "Edited"


# ---- approved execution ----------------------------------------------------------------------
async def test_approved_call_executes_once_and_the_row_ends_executed_with_the_approver() -> None:
    env = Env()
    pending = await env.propose()
    aid = pending.approval_id or ""
    assert (await env.approve(aid)).outcome == "approved"
    result = await env.run_approved(aid)
    assert result.status == "ok" and len(env.sent) == 1
    assert env.state(aid) == "EXECUTED"
    assert env.repo.records[aid].actor == "USER"  # CONSUMED/EXECUTED must not erase who approved
    assert [t[2] for t in env.repo.transitions] == ["APPROVED", "CONSUMED", "EXECUTED"]
    assert next(iter(env.executions.rows.values()))["state"] == "FINISHED"
    assert {"approval.approved", "approval.consumed", "approval.executed"} <= set(env.audit.types())


async def test_defaults_are_normalized_so_equivalent_arguments_hash_the_same() -> None:
    env = Env()
    pending = await env.propose(args={"recipient": "a@b.c", "body": "x"})
    aid = pending.approval_id or ""
    await env.approve(aid)
    # the model re-sends the same call spelled with the default made explicit
    result = await env.run_approved(aid, args={"recipient": "a@b.c", "body": "x", "cc": None})
    assert result.status == "ok"


async def test_mutating_the_payload_after_approval_prevents_execution() -> None:
    env = Env()
    pending = await env.propose(args={"recipient": "boss@example.com", "body": "Report"})
    aid = pending.approval_id or ""
    await env.approve(aid)
    result = await env.run_approved(
        aid, args={"recipient": "attacker@evil.example", "body": "Report"}
    )
    assert result.status == "denied" and result.error_code == "approval_invalidated"
    assert env.sent == [] and env.state(aid) == "INVALIDATED"
    assert any(
        isinstance(e, events.ApprovalResolved) and e.outcome == "invalidated" for e in env.events
    )


async def test_state_changed_after_approval_voids_it_and_nothing_runs() -> None:
    env = Env()
    env.disk["/work/a.md"] = "v1"
    pending = await env.propose("files.edit", {"path": "/work/a.md"})
    aid = pending.approval_id or ""
    assert "_resolved_targets" in env.repo.records[aid].payload_preview
    await env.approve(aid)
    env.disk["/work/a.md"] = "v2"  # the file changed after the user looked
    result = await env.run_approved(aid, "files.edit", {"path": "/work/a.md"})
    assert result.error_code == "approval_invalidated"
    assert env.edited == [] and env.state(aid) == "INVALIDATED"


async def test_unchanged_state_runs_and_a_replay_survives_drift_on_the_same_approval() -> None:
    env = Env()
    env.disk["/work/a.md"] = "v1"
    first = await env.propose("files.edit", {"path": "/work/a.md"})
    env.disk["/work/a.md"] = "v2"  # resume re-runs the node: it must find the approval, not fork
    again = await env.propose("files.edit", {"path": "/work/a.md"})
    assert again.approval_id == first.approval_id and len(env.repo.records) == 1
    env.disk["/work/a.md"] = "v1"
    await env.approve(first.approval_id or "")
    ran = await env.run_approved(first.approval_id or "", "files.edit", {"path": "/work/a.md"})
    assert ran.status == "ok" and env.edited == ["/work/a.md"]


async def test_an_edited_approval_runs_the_stored_edit_not_what_the_caller_sends() -> None:
    env = Env()
    pending = await env.propose(args={"recipient": "boss@example.com", "body": "Draft"})
    aid = pending.approval_id or ""
    resolution = await env.broker.resolve(
        aid, decision="edit", edited_args={"recipient": "boss@example.com", "body": "Final"}
    )
    assert resolution.outcome == "edited"
    rec = env.repo.records[aid]
    assert rec.original_payload_hash and rec.original_payload_hash != rec.payload_hash
    # the caller replays the ORIGINAL arguments; the engine must run the approved edit
    result = await env.run_approved(aid, args={"recipient": "boss@example.com", "body": "Draft"})
    assert result.status == "ok"
    assert env.sent[0]["body"] == "Final"


async def test_an_edit_may_not_raise_risk_and_the_approval_stays_pending() -> None:
    env = Env()
    pending = await env.propose("files.edit", {"path": "/work/notes.txt"})  # R2
    aid = pending.approval_id or ""
    refused = await env.broker.resolve(aid, decision="edit", edited_args={"path": "/etc/hosts"})
    assert refused.outcome == "refused" and "raises the risk" in (refused.detail or "")
    assert env.state(aid) == "PENDING"
    invalid = await env.broker.resolve(aid, decision="edit", edited_args={"nope": 1})
    assert invalid.outcome == "refused"
    # a same-risk edit is fine
    ok = await env.broker.resolve(aid, decision="edit", edited_args={"path": "/work/other.txt"})
    assert ok.outcome == "edited"
    assert (await env.run_approved(aid, "files.edit", {"path": "/work/notes.txt"})).status == "ok"
    assert env.edited == ["/work/other.txt"]


async def test_double_click_race_executes_once_and_does_not_corrupt_the_winner() -> None:
    env = Env()
    pending = await env.propose()
    aid = pending.approval_id or ""
    await env.approve(aid)
    results = await asyncio.gather(*(env.run_approved(aid) for _ in range(8)))
    assert [r.status for r in results].count("ok") == 1
    assert len(env.sent) == 1
    losers = [r for r in results if r.status != "ok"]
    assert {r.error_code for r in losers} <= {"approval_already_used"}
    assert env.state(aid) == "EXECUTED"  # a loser must not flip the winner to EXEC_FAILED


async def test_an_expired_approval_cannot_be_consumed() -> None:
    env = Env()
    pending = await env.propose()
    aid = pending.approval_id or ""
    await env.approve(aid)
    env.clock.advance(timedelta(hours=3))  # R3 TTL is 2h; the sweep has not run yet
    result = await env.run_approved(aid)
    assert result.error_code == "approval_expired" and env.sent == []


async def test_approving_after_expiry_is_reported_as_expired_and_settles_the_row() -> None:
    env = Env()
    aid = (await env.propose()).approval_id or ""
    env.clock.advance(timedelta(hours=3))
    resolution = await env.approve(aid)
    assert resolution.outcome == "expired"
    assert env.state(aid) == "EXPIRED" and "approval.expired" in env.audit.types()


async def test_rejected_approval_never_executes() -> None:
    env = Env()
    aid = (await env.propose()).approval_id or ""
    assert (await env.broker.resolve(aid, decision="reject", comment="no")).outcome == "rejected"
    result = await env.run_approved(aid)
    assert result.error_code == "approval_rejected" and env.sent == []


async def test_approval_belongs_to_one_call() -> None:
    env = Env()
    aid = (await env.propose(run_id="run_1")).approval_id or ""
    await env.approve(aid)
    other_run = await env.run_approved(aid, run_id="run_2")
    assert other_run.error_code == "approval_mismatch"
    other_tool = await env.run_approved(aid, "files.edit", {"path": "/work/a"})
    assert other_tool.error_code == "approval_mismatch"
    missing = await env.run_approved("apr_nope")
    assert missing.error_code == "approval_not_found" and env.sent == []


# ---- policy can only tighten after the fact ---------------------------------------------------
async def test_policy_tightened_after_approval_blocks_execution() -> None:
    env = Env()
    aid = (await env.propose()).approval_id or ""
    await env.approve(aid)
    deny = PolicyConfig(overrides=[ToolPolicy("mail.send_message", PolicyDecision.DENY)])
    env.engine._policy = PolicyEngine(
        deny
    )  # the user ran `mindos policy tighten --deny` and restarted
    result = await env.run_approved(aid)
    assert result.error_code == "policy_denied" and env.sent == []
    assert env.state(aid) == "INVALIDATED"


async def test_switching_to_plan_mode_after_approval_blocks_execution() -> None:
    mode = SessionMode()
    env = Env(mode=mode)
    aid = (await env.propose()).approval_id or ""
    await env.approve(aid)
    mode.set(ApprovalMode.PLAN)
    assert (await env.run_approved(aid)).error_code == "policy_denied"
    assert env.sent == []


async def test_plan_mode_denies_a_new_side_effect_outright() -> None:
    env = Env(mode=SessionMode(ApprovalMode.PLAN))
    result = await env.propose()
    assert result.error_code == "policy_denied" and env.repo.records == {}


# ---- taint ------------------------------------------------------------------------------------
async def test_reading_untrusted_content_taints_the_run_and_escalates_an_auto_allowed_call() -> (
    None
):
    env = Env()
    draft = {"recipient": "a@example.com", "body": "x"}
    assert (await env.propose("mail.create_draft", draft, call_id="d0")).status == "ok"  # clean
    assert env.taint.is_tainted("run_1") is False
    assert (await env.propose("mail.search", {"q": "invoice"}, call_id="s1")).status == "ok"
    assert env.taint.sources("run_1") == {TaintSource.TOOL_OUTPUT}
    tainted = await env.propose("mail.create_draft", draft, call_id="d1")
    assert tainted.status == "pending_approval"  # an auto-run R1 now needs the user
    view = next(e for e in env.events if isinstance(e, events.ApprovalRequested)).approval
    assert view.tainted is True
    # another run is unaffected, and ending a run clears its taint
    assert (
        await env.propose("mail.create_draft", draft, run_id="run_2", call_id="d2")
    ).status == "ok"
    env.engine.end_run("run_1")
    assert env.taint.is_tainted("run_1") is False


async def test_a_resumed_run_regains_the_taint_it_was_approved_under() -> None:
    env = Env()
    await env.propose("mail.search", {"q": "x"})
    draft = {"recipient": "a@example.com", "body": "x"}
    aid = (await env.propose("mail.create_draft", draft)).approval_id or ""
    assert env.repo.records[aid].tainted is True
    await env.approve(aid)
    env.engine.end_run("run_1")  # a new process: nothing in memory
    assert (await env.run_approved(aid, "mail.create_draft", draft)).status == "ok"
    assert env.taint.is_tainted("run_1")  # restored from the approval record


# ---- execution log and cancellation -----------------------------------------------------------
async def test_a_side_effect_is_recorded_started_before_it_runs() -> None:
    env = Env()
    seen: list[str] = []

    async def handler(args: Any, ctx: Any) -> str:
        seen.append(next(iter(env.executions.rows.values()))["state"])
        return "ok"

    spec = env.registry.get("mail.create_draft")
    assert spec is not None
    object.__setattr__(spec, "handler", handler)
    await env.propose("mail.create_draft", {"recipient": "a@b.c", "body": "x"})
    assert seen == ["STARTED"]
    assert next(iter(env.executions.rows.values()))["state"] == "FINISHED"


async def test_the_exact_same_side_effect_call_is_never_run_twice() -> None:
    env = Env()
    args = {"recipient": "a@b.c", "body": "x"}
    first = await env.propose("mail.create_draft", args)
    second = await env.propose("mail.create_draft", args)  # e.g. a graph replay after a crash
    assert first.status == "ok" and second.error_code == "execution_not_recorded"
    assert env.drafts == ["a@b.c"]


async def test_cancellation_mid_execution_leaves_the_outcome_unknown_not_failed() -> None:
    env = Env()
    started = asyncio.Event()

    async def hang(args: Any, ctx: Any) -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    spec = env.registry.get("mail.send_message")
    assert spec is not None
    object.__setattr__(spec, "handler", hang)
    aid = (await env.propose()).approval_id or ""
    await env.approve(aid)
    task = asyncio.create_task(env.run_approved(aid))
    await started.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert env.state(aid) == "CONSUMED"  # reconciliation settles it as unknown at next start
    assert next(iter(env.executions.rows.values()))["state"] == "STARTED"
    # and a re-delivery cannot run it a second time
    assert (await env.run_approved(aid)).error_code == "approval_already_used"


async def test_principal_is_part_of_the_approval() -> None:
    env = Env()
    aid = (await env.propose()).approval_id or ""
    await env.approve(aid)
    inv = env.invocation(approval_id=aid).model_copy(update={"principal": Principal.JOB})
    assert (await env.run(inv)).error_code == "approval_mismatch"


async def test_r2_risk_comes_from_the_classifier_for_approvals() -> None:
    env = Env()
    result = await env.propose("files.edit", {"path": "/work/a.txt"})
    assert env.repo.records[result.approval_id or ""].risk is RiskClass.R2


# ---- auth failures from an adapter (ARCHITECTURE 6.3) ----------------------------------------
async def test_a_reauth_failure_tells_the_user_how_and_is_never_retried() -> None:
    env = Env()
    env.auth_error = ReauthRequired(
        "google", "google needs you to sign in again. Run: mindos auth google"
    )
    result = await env.propose("mail.fetch", {"q": "x"})
    assert result.status == "error" and result.error_code == "auth_required"
    assert "mindos auth google" in result.content and "do not retry" in result.content
    assert env.fetches == 1  # a read tool is retried on transient errors, not on auth
    auth = [e for e in env.events if isinstance(e, events.AuthRequired)]
    assert [(e.provider, e.scope_delta) for e in auth] == [("google", ())]


async def test_a_missing_scope_carries_the_delta_in_the_event() -> None:
    env = Env()
    env.auth_error = ScopeRequired("google", "needs more permission", ("gmail.send",))
    result = await env.propose("mail.fetch", {"q": "x"})
    assert result.error_code == "needs_scope"
    assert [e.scope_delta for e in env.events if isinstance(e, events.AuthRequired)] == [
        ("gmail.send",)
    ]


async def test_an_unreachable_provider_is_reported_without_asking_for_a_sign_in() -> None:
    env = Env()
    env.auth_error = TransientAuthError("google", "google is unavailable; try again shortly.")
    result = await env.propose("mail.fetch", {"q": "x"})
    assert result.error_code == "auth_unavailable"
    assert not [e for e in env.events if isinstance(e, events.AuthRequired)]
