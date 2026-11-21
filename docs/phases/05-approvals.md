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
