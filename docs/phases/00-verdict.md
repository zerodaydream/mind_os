# 00 · Architecture Verdict

**Verdict: strong and fit for purpose. Build it, with 6 fixes below.** Not "perfect": it has gaps that would hurt if built as written.

## Why it fits
- Single user, single Mac, correctness > scale → modular monolith is right.
- Engine as only path to adapters + payload-hashed approvals + taint escalation = real security, not prompt-based.
- Checkpoint-resume via LangGraph + Postgres is verified (docs.langchain.com). `interrupt_on` + `reject` (not `respond`) is correct.
- Events-only TUI boundary lets us build the whole Claude Code UI against a fake agent first.

## Fixes (APPLIED to `docs/ARCHITECTURE.md` and `docs/README.md`)
| # | Problem | Fix |
|---|---|---|
| 1 | **Phase order bug.** Indexer/watcher (Ph 7) needs `jobs` + worker (Ph 9). | Jobs/worker before FS intelligence. |
| 2 | **TUI too thin.** Original Ph 1 = "minimal shell". Exact Claude Code UI is a large workstream. | Own phase, built on a scripted fake event stream. |
| 3 | **Event vocabulary lacks Claude Code UX.** Missing: thinking deltas, usage/cost, permission-mode change, slash-command output, rewind. | Add `ThinkingDelta`, `UsageUpdated`, `ModeChanged`, `CommandOutput`, `Rewound` events. |
| 4 | **DeepAgents risk.** Pre-1.0 (v0.7.x), moves fast; planning tool removed by default in 0.7; built-in fs/shell tools must be disabled (§14, unverified). | Phase 1 spike. Wrap behind an `AgentRuntime` port. Resolved: ADR 0001 (lockdown works), ADR 0003 (plain agent for Phase 04), ADR 0007 (Deep Agents adopted, lockdown kept). |
| 5 | **Checkpointer ≠ crash recovery.** Nothing restarts a dead run. | Startup reconciliation (§11.4) is mandatory scope in Approvals phase, not optional. |
| 6 | **Postgres is heavy** for one user. | Keep (pgvector, SKIP LOCKED, NOTIFY justify it). Ship `docker-compose` + `mindos doctor`. Record embedded-mode ADR, don't build it. |

## Smaller notes
- `search_tools`/`load_tools` is premature while each subagent has <15 tools. Keep gated on measurement (already is).
- Sandbox (Ph 13) stays deferred. Correct.
- 6 subagents + orchestrator is a lot of prompt/eval surface. Ship Calendar-read first, add one namespace at a time, each behind evals.
- `AGENTS.md` in repo root currently holds **Astryx web-UI rules** (React, no `<div>`). Irrelevant to a Python/Textual TUI. Replace in Phase 2 (foundation).
- Existing working tree has the old `backend/` staged as deleted. Commit that removal as its own commit first.

## Claude Code UI: what I verified
- Two renderers exist: **classic** (native scrollback) and **fullscreen** (alternate screen, fixed input box, in-app scroll/search). Source: code.claude.com/docs/en/fullscreen.
- Textual = alternate-screen app → matches **fullscreen mode** exactly. Classic scrollback mode is **not** reproducible in Textual. Accepted trade-off.
- Claude Code uses a custom Ink (React) renderer. We do not copy code or assets; we reproduce layout and behavior under our own name.
- Glyph/layout details in Phase 03 come from public docs + behavior; **verify against a live `claude` session in Phase 01 spike**.

## Phases
File number = architecture phase + 1 (`01-spikes.md` is ARCHITECTURE Phase 0).

| File | Phase |
|---|---|
| 01-spikes.md | Verify versions and risks |
| 02-foundation.md | Skeleton, config, DB, bus |
| 03-tui.md | Claude Code UI on fake events |
| 04-engine.md | Tool engine + first subagent |
| 05-approvals.md | Policy, approvals, resume |
| 06-auth.md | Google + Microsoft OAuth |
| 07-namespaces.md | Mail, Calendar, Teams, Files, Convert, System |
| 08-jobs.md | Jobs, scheduler, worker |
| 09-fs-intelligence.md | Search tiers + indexer |
| 10-memory.md | Memory with provenance |
| 11-tool-discovery.md | Dynamic tools (gated) |
| 12-mcp.md | MCP (gated by allowlist) |
| 13-sandbox.md | Sandbox (only with evidence) |

Skills are *recommendations*. Install counts come from `npx skills find` today; repo star counts were not checked. Install only after review: `npx skills add <owner/repo@skill>`.
