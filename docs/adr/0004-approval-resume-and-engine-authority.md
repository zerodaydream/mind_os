# ADR 0004: Approvals pause through LangGraph, but only the engine and the database decide

Status: Accepted (Phase 05 review)
Date: 2026-11-24
Versions: `langgraph` 1.2.24 (interrupt ids on parallel interrupts), PostgreSQL 16

## Context
The first Phase 05 build passed its unit tests on fakes and was wrong on the real graph. Running a
scripted model against `create_agent` showed that LangGraph re-runs a tool node from the top when it
resumes, that two approval-gated calls in one turn are two pending interrupts that need their ids,
and that an interrupt raised inside a subagent is swallowed as a failed delegation.

## Decision
- **The engine is the only authority.** After `interrupt()` returns, the bridge calls the engine again
  and the engine reads the approval's state from the database. What the UI put in the resume value is
  used only to word a rejection. An approved call runs the arguments stored on the approval row (an
  edit runs the stored edit), re-validated, re-classified, re-hashed, and re-checked against policy,
  and only then claimed with one atomic `UPDATE ... WHERE state IN ('APPROVED','APPROVED_EDITED') AND
  expires_at > now()`.
- **Everything before `interrupt()` is idempotent**, because it runs again on resume. The broker
  returns the existing approval for the same `(run, tool_call, payload)` key and says `created=False`,
  so a replay creates no second row and announces nothing. A replay also stays on its approval even
  though the run's in-memory taint is gone, instead of asking policy again.
- **The checkpoint says what is paused.** `resume()` reads the pending interrupts from the checkpoint,
  takes the run's ids and the interrupt id from there, and answers one interrupt at a time. Nothing
  paused on that approval means it was already delivered: a no-op.
- **Recovery is driven by `approvals.delivered_at`.** An approval row records its `thread_id` and
  `conversation_id`. At start, `reconcile_storage` repairs database-only state (expire, mark in-flight
  executions `UNKNOWN`, settle `CONSUMED` rows), and `Application.recover` re-shows pending approvals
  and delivers decisions the paused graph never got. An approval that expired while the app was closed
  is reported, not resumed unattended.
- **Unknown outcomes are recorded as unknown.** A side effect that was `STARTED` when the process died
  becomes `UNKNOWN`, never `FINISHED` or `FAILED`.
- **The database enforces what the code relies on**: forward-only transitions, an immutable approved
  payload and identity, and no approving or consuming an expired row, all in one trigger. A test checks
  the trigger against `ALLOWED_TRANSITIONS` for all 100 state pairs.
- **Taint is set by code.** The engine marks a run after a tool with untrusted output returns, and the
  approval row keeps the flag so a resumed run regains it.

## Open decision: approvals raised inside a subagent
A subagent has no checkpointer (ADR 0003), so it cannot pause. Until this is decided, a call that needs
approval from a subagent **fails closed**: `approval_unavailable`, no approval row, and the subagent
reports it. Architecture 3.2 expects `Delegating -> AwaitingApproval`; this does not deliver it.
Options: (a) let subagent graphs inherit the checkpointer, let the interrupt propagate through the
`delegate` tool, and re-enter the subagent on resume; (b) have the subagent return
`needs_input` with `pending_approvals`, and have the orchestrator execute the approved call itself.
This must be decided before the first subagent tool with a side effect (Mail send, Phase 06+).

## Not yet in the payload hash
Architecture 5.4 also hashes `resolved_targets` and `base_state_hashes`. No tool has either yet, so the
hash covers tool, version, principal, and normalized arguments. Add both with the first tool that needs
them (file edits, mail send); the edit flow must recompute them through the engine.
