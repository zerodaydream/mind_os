# 05 · Approvals, Policy, Resume

Maps to ARCHITECTURE §5, §11.4, §14.4. Highest-risk phase; do not rush.

## Skills
| Skill | Source |
|---|---|
| `langgraph-human-in-the-loop` | `langchain-ai/langchain-skills` (18K) |
| `langgraph-persistence` | same (19K) |
| `llm-security` | `semgrep/skills` (1.8K, review first) |
| `code-review` (mode: review) | local |
| `test-coverage` | local |
| `security-setup` | local |

## Build
- `risk` classifier R0–R4 from validated args + resolved targets.
- `PolicyEngine.decide()`; `policy.toml`; `mindos policy …` (user-only, audited). Model has no policy tool.
- Taint tracking per run; escalation rule for outbound actions.
- Approval state machine + repository: unique `idempotency_key`, atomic `CONSUMED` claim, TTL, payload hash (`base_state_hashes`).
- Approval broker + `ApprovalRequested/Resolved/Expired`; bridge to `interrupt_on` (transport only; engine fails closed independently).
- Decisions: approve / edit (re-validate, re-classify, new hash) / reject (reason).
- **Startup reconciliation** (§11.4 steps 1–5) — required here.
- TUI: wire Phase 03 permission dialog to real broker; pending-approval badge; `mindos resume`.
- Batch approvals table (`approval_batches`) created now, used by Phase 13.

## Gate (all must pass)
- Exhaustive state-machine + Hypothesis tests on hash canonicalization.
- Mutate payload after approval ⇒ no execution.
- Double-click / race ⇒ one execution.
- Kill process between approve and consume ⇒ resumes, executes once.
- Injection corpus: tainted run cannot send to new recipient without approval.

## Don't
- Never use `respond` to deny a side-effect tool; use `reject`.

## Status (reviewed and corrected 2026-11-24)
Decisions and the reasoning are in `docs/adr/0004-approval-resume-and-engine-authority.md`.

The first build (2026-11-23) reported "195 tests passing, all gates green". That was not true: one
integration test failed (the test itself was broken), the state-machine "exhaustive" test was a
tautology, the phase gate's Hypothesis tests did not exist (now added), and nothing ran a real LangGraph
pause/resume. A review against the real graph found the defects below, now fixed.

Fixed in review:
- **Resume re-ran the tool node**: every resume created a second orphan `PENDING` approval, lost the
  run context (no `tool.finished` event, no audit `run_id`), and two approvals in one turn crashed with
  `multiple pending interrupts`. Requests are now idempotent, the run and interrupt ids come from the
  checkpoint, and parallel approvals resume one at a time.
- **Expiry was never enforced at claim time**: `consume` ignored `expires_at`, so an expired but
  unswept approval could execute. It is now part of the atomic `UPDATE` and of the DB trigger.
- **The engine trusted the caller on the approved path** and never re-checked policy or risk. It now
  runs the stored (possibly edited) payload, re-validates, re-classifies, re-hashes, re-checks policy,
  and rejects an edit that raises risk.
- **Race loser corrupted the winner**: a failed claim marked the winner's row `EXEC_FAILED`; `actor`
  was wiped to NULL by every engine transition. Settlement now happens only after a successful claim.
- **Taint tracking was dead code** (a constant `False`). The engine now taints a run after untrusted
  tool output and restores it on resume.
- **Reconciliation step 2 was missing** and `tool_executions` was never written. Side effects are now
  recorded `STARTED` before the call; recovery re-delivers decisions; in-flight effects become `UNKNOWN`.
- **Quitting the app cancelled pending approvals** (`aclose` ran the cancel path).
- **Policy**: a corrupt `policy.toml` silently fell back to defaults and invalid entries were dropped;
  plan mode was displayed but never enforced; `mindos policy` was not audited; `R2 = false` meant DENY.
  Parsing is now strict and fails startup, plan mode is enforced, changes are audited and written
  atomically (0600), R3/R4 cannot be relaxed.
- Dead or stale code removed: `ReadOnlyPolicy` (moved to tests), the `TypeError` fallback around
  `policy.decide`, `find_started_executions`, `pending_for_run`, and `mindos resume`'s ignored argument.

Closed on 2026-11-25 (ADR 0005):
- **Approvals inside a subagent** are deferred: queued, the run finishes, and the application runs
  the call as the asking agent once the user approves. The model is not resumed.
- **`resolved_targets` / `base_state_hashes`** are in the payload hash (`BoundState`,
  `ToolSpec.bind_state`). No tool uses them yet.
- **Reasoning effort** of the original run is kept in the checkpoint and used on resume.

Known gaps:
- `approval_batches` exists but nothing uses it until Phase 13.
- Migrations `0004` and `0005` must be applied (`uv run mindos db migrate`) before the app will start.

Gate: `ruff`, `mypy`, 9 import-linter contracts, and `pytest` all pass, including the Postgres
integration tests (20-way claim race, trigger parity for all 100 state pairs) and the real-graph
resume tests (single, parallel, restart into a fresh runtime, crash between approve and consume).
