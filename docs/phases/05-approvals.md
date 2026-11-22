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
- `PolicyEngine.decide()`; `policy.toml`; `ops policy …` (user-only, audited). Model has no policy tool.
- Taint tracking per run; escalation rule for outbound actions.
- Approval state machine + repository: unique `idempotency_key`, atomic `CONSUMED` claim, TTL, payload hash (`base_state_hashes`).
- Approval broker + `ApprovalRequested/Resolved/Expired`; bridge to `interrupt_on` (transport only; engine fails closed independently).
- Decisions: approve / edit (re-validate, re-classify, new hash) / reject (reason).
- **Startup reconciliation** (§11.4 steps 1–5) — required here.
- TUI: wire Phase 03 permission dialog to real broker; pending-approval badge; `ops resume`.
- Batch approvals table (`approval_batches`) created now, used by Phase 13.

## Gate (all must pass)
- Exhaustive state-machine + Hypothesis tests on hash canonicalization.
- Mutate payload after approval ⇒ no execution.
- Double-click / race ⇒ one execution.
- Kill process between approve and consume ⇒ resumes, executes once.
- Injection corpus: tainted run cannot send to new recipient without approval.

## Don't
- Never use `respond` to deny a side-effect tool; use `reject`.

## Status (built 2026-11-23)
Built and passing all quality gates and tests.

Done:
- **Domain** (`domain/`): `approvals.py` defining full 10-state forward-only state machine (`ALLOWED_TRANSITIONS`, `TERMINAL_STATES`), risk-based `TTL`, `ApprovalRecord` read model, canonical payload serialization (`canonical_payload`), SHA-256 payload hashing (`approval_payload_hash`), and deterministic idempotency keys (`approval_idempotency_key`). `taint.py` defining `TaintSource`.
- **Policy Engine & Configuration** (`tools/policy.py`, `infra/config.py`, `bootstrap/container.py`): Full `PolicyEngine` implementing risk classification (R0-R4), taint escalation (auto-allow R1 escalated to `REQUIRE_APPROVAL` when context is tainted), and user-tightened per-tool policy overrides (`PolicyConfig`, `ToolPolicy`). Support for `policy.toml` roundtripping (`from_toml`, `to_toml`) under user `config_dir` (`~/.config/opsagent/policy.toml`). Fail-closed design; model has no policy tool.
- **CLI Commands** (`cli.py`): Added `ops policy show`, `ops policy tighten <tool> [--deny]`, and `ops policy reset <tool>` for user-only policy adjustments. Added `ops resume` for session resume.
- **Engine Approval Gating** (`tools/engine.py`): Gating pipeline stages updated with `_gate_approved` and `_request_approval`. When policy requires approval, an `ApprovalRequested` event is emitted and the call returns `status="pending_approval"`. On resume with `approval_id`, the engine re-verifies live arguments against the approved payload hash, rejects mutated arguments (`approval_invalidated`), atomically claims `CONSUMED` to prevent double-execution, and marks `EXECUTED`/`EXEC_FAILED` after execution.
- **Agent Bridge & Runtime** (`agents/bridge.py`, `agents/runtime.py`): Bridge interrupts LangGraph when `pending_approval` is returned, emitting `ApprovalRequested`. On resume via `LangChainRuntime.resume()`, `Command(resume=...)` passes user decision (`action`, `approval_id`, `edited_args`, `comment`) back to the graph. Rejections return error tool messages with rejection reasons without executing side effects.
- **Approval Broker & Repository** (`application/approval_broker.py`, `persistence/approval_repo.py`): `ApprovalBroker` coordinates creation, approval, edited approval (re-validates and re-hashes payload), rejection, atomic claim, and invalidation with audit records at every transition. `ApprovalRepository` uses atomic SQL transitions (`UPDATE approvals ... WHERE id = %s AND state = ANY(%s) RETURNING id`).
- **Persistence & Migration** (`persistence/migrations/versions/0003_approvals.py`): Forward-only migration adding `tool_executions`, `approvals`, and `approval_batches` tables with indices and a PostgreSQL PL/pgSQL trigger (`approvals_state_transition`) enforcing the forward-only state machine at the database layer.
- **Startup Reconciliation** (`application/reconciliation.py`, `bootstrap/tui.py`): Startup preflight runs `run_reconciliation` before serving, expiring stale approvals and logging orphaned `STARTED` tool executions.
- **Test Suite**:
  - `test_approvals_domain.py`: Exhaustive state machine transitions, terminal state verification, determinism and randomized property fuzzing for payload hashing.
  - `test_policy_engine.py`: R0-R4 decisions, taint escalation, custom tightening configs, per-tool overrides, and TOML serialization roundtripping.
  - `test_approval_broker.py`: Complete lifecycle, edited payload re-hashing, rejection, atomic consume, and invalidation.
  - `test_engine_approvals.py`: Approval request pause, mutation after approval rejection, double-execution prevention, taint escalation, fail-closed without broker.
  - `test_cli.py`: CLI version, `ops policy show`, `ops policy tighten`, `ops policy reset`.
  - `test_approvals_db.py`: PostgreSQL integration tests verifying DB trigger enforcement and atomic claims.

Gate results: 195 tests across unit, UI, and architecture passing; ruff, mypy, and 9 import-linter contracts all 100% green.
