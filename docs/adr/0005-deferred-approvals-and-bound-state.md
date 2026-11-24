# ADR 0005: Subagent approvals are deferred; approvals bind to target state

Status: Accepted (closes the open decisions in ADR 0004)
Date: 2026-11-25

## Context
ADR 0004 left three gaps: a subagent that needs approval failed closed, the payload hash did not
cover `resolved_targets` or `base_state_hashes` (ARCHITECTURE 5.4), and a resumed run lost the
reasoning effort it started with.

## Decision

### Subagent approvals are deferred, not awaited
A subagent graph has no checkpointer, and a LangGraph re-run of the delegating tool node would
run the whole subagent again (new model calls, new tool call IDs, duplicate approvals). So the
run does not wait.

- The engine creates the approval as usual, with `agent` (who asked) and `deferred = true`, and
  tells the subagent the call was queued and must not be retried (`[approval_queued:...]`).
- The subagent returns `SubagentResult(status="needs_input", pending_approvals=...)`, built by
  code from its ledger. The orchestrator tells the user and the run finishes normally.
- `ApprovalRequested` carries `deferred`. The UI keeps a deferred approval after its run ends.
- When the user decides, `Application` records the decision and, if approved, calls
  `ApprovedCallRunner.run_approved` (implemented by the engine). The engine takes the arguments
  from the approval row and applies the same verification and atomic claim as any approved call,
  as the recorded `agent`. The user gets a notification with the true outcome.
- The model is not resumed. It never sees the outcome; the user does. If the outcome matters to a
  later turn, the user says so. This is the cost of the choice, and it is deliberate.
- Recovery: a pending deferred approval is shown again after a restart; an approved one that never
  ran is run (the claim makes it once); a settled one is only marked delivered.
- `agent` and `deferred` are immutable in the database trigger, so the agent allowed to run a call
  cannot change after the user saw it (migration 0005).

Architecture 3.2 (`Delegating -> AwaitingApproval`) is therefore not how a subagent approval
works. The subagent finishes `needs_input`; the approval outlives the run.

### Approvals bind to target state
A tool may declare `bind_state(args) -> BoundState` with `resolved_targets` and
`base_state_hashes`. Both join the payload hash. The engine recomputes them when it verifies an
approved call; any drift invalidates the approval (`state_changed`) and nothing runs. An edit
recomputes them through the engine. The approval preview shows the resolved targets.

The idempotency key still uses the arguments alone. A replay after a resume therefore finds its
approval even if the target changed meanwhile, and the drift is caught at verification instead of
forking a second approval. A handler must still re-check at the moment it acts: the window between
verification and the call is small, not zero.

### Reasoning effort survives a resume
The runtime writes the effort into the checkpoint metadata of every run. `resume` reads it back
from the paused checkpoint. A missing or unknown value falls back to the default.

## Consequences
- The first tool with a side effect on a file or mail thread must provide `bind_state`.
- No tool provides `bind_state` yet, so the hash of every current tool is unchanged.
- Batch approvals (Phase 13) can reuse `deferred` for work no graph waits on.
