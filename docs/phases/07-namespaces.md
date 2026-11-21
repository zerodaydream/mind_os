# 07 · Namespaces (Mail, Calendar writes, Teams, Files basic, Convert, System)

Maps to ARCHITECTURE §3.4, §7, §11.3. **One namespace per sub-step**, each with evals before the next.

## Skills
| Skill | Source |
|---|---|
| `tool-design` | local |
| `multi-agent-patterns` | local |
| `langchain-eval-engineering` | local |
| `entra-app-registration` | `microsoft/azure-skills` (Teams) |
| `python-testing-patterns` | `wshobson/agents` |
| `code-review` | local |

## Order
1. **Calendar writes** (smallest; attendee-bearing ⇒ approval).
2. **Mail** (search/get/draft/send; idempotent send via client header + Sent reconcile).
3. **Files basic** (read, diff-edit, move, delete; roots + denylist; symlink resolve; base-state hash).
4. **Convert** (no network; output dir; pick converter ADR).
5. **Teams** (list chats, read, send; no auto-retry after ambiguity).
6. **System** (named allowlisted macOS actions; no shell/AppleScript from model text).

## Each namespace ships
- `ToolSpec`s (coarse, versioned `name@major`), risk classifier, retry/idempotency, redactions.
- Subagent definition + prompt + result contract.
- Tests: validation, risk, approval, idempotency; contract tests; failure-injection; ≥5 eval scenarios incl. injection.
- Untrusted content tagged (email HTML stripped, links not fetched, attachments never executed).

## Gate
- Per-namespace evals pass. Ambiguous-failure reconcile test per side-effect tool.
