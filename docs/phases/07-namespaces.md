# 07 · Namespaces (Calendar writes, Mail, Files basic, Convert, System)

Maps to ARCHITECTURE §3.4, §7, §11.3. **One namespace per sub-step**, each with evals before the next.

## Skills
| Skill | Source |
|---|---|
| `tool-design` | local |
| `multi-agent-patterns` | local |
| `langchain-eval-engineering` | local |
| `python-testing-patterns` | `wshobson/agents` |
| `code-review` | local |

## Order
1. **Calendar writes** (smallest; attendee-bearing ⇒ approval).
2. **Mail** (search/get/draft/send; idempotent send via client header + Sent reconcile).
3. **Files basic** (read, diff-edit, move, delete; roots + denylist; symlink resolve; base-state hash).
4. **Convert** (no network; output dir; pick converter ADR).
5. **System** (named allowlisted macOS actions; no shell/AppleScript from model text).

## Each namespace ships
- `ToolSpec`s (coarse, versioned `name@major`), risk classifier, retry/idempotency, redactions.
- Subagent definition + prompt + result contract.
- Tests: validation, risk, approval, idempotency; contract tests; failure-injection; ≥5 eval scenarios incl. injection.
- Untrusted content tagged (email HTML stripped, links not fetched, attachments never executed).

## Gate
- Per-namespace evals pass. Ambiguous-failure reconcile test per side-effect tool.

## Status (2026-11-25)
Teams is out of scope (ADR 0008); the order above no longer has it. Decisions: `docs/adr/0009-calendar-writes-and-google-calendar.md`.

**1. Calendar writes: built and tested, not run against real Google.**
- `calendar.create_event|update_event|delete_event`, risk by who is affected, approval bound to the
  event (label shown, fingerprint hashed), idempotent create, honest unknown-outcome messages.
- `CalendarWriter` port, `FixtureCalendar` writer, and `GoogleCalendar` (reads and writes) with
  401 refresh, scope errors, throttling, and create reconcile by key.
- The calendar subagent proposes writes; its approvals are deferred and the app runs them.
- Tests: validation, risk, approval, bound-state drift, idempotency, ambiguous-failure,
  taint, adapter failure injection, and an end-to-end subagent write.

Not done: five real-model eval scenarios, a live Google run, etags, recurring-series edits.
Next in order: **2. Mail**.
