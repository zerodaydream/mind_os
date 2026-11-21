# 08 · Jobs, Scheduler, Worker

Maps to ARCHITECTURE §12, §11.4. **Moved before FS intelligence** (indexer runs on this).

## Skills
| Skill | Source |
|---|---|
| `python-background-jobs` | `wshobson/agents` (11.9K) |
| `postgresql-optimization` | `github/awesome-copilot` (17K) |
| `postgresql-table-design` | `wshobson/agents` |
| `python-testing-patterns` | `wshobson/agents` |

## Build
- `jobs`, `job_runs`; claim via `FOR UPDATE SKIP LOCKED`; lease + heartbeat; partial index on runnable.
- States: `SCHEDULED → RUNNING → SUCCEEDED | FAILED | CANCELLED | WAITING_APPROVAL`; dead-letter + notification.
- `ops worker` process; same engine, principal `JOB`; jobs never self-approve.
- Time: `timestamptz` + IANA zone, recurrence in local zone, DST rules (nonexistent → shift forward, ambiguous → first). Tested.
- `LISTEN/NOTIFY` → TUI badge/toast; macOS notification adapter (policy-gated).
- Job types now: reminders, scheduled agent runs, token health check, retention pruning.
- launchd plist example.

## Gate
- Worker killed mid-job ⇒ lease expiry ⇒ one re-claim, idempotent.
- DST tests (spring forward, fall back). Concurrent claim test.
