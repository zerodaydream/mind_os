# Code Review Report

**Date**: 2026-11-20  
**Scope**: Architecture v2 — `feature/arch-v2` vs `main`, plus uncommitted working tree (orchestrate, v2 planes, models)  
**Mode**: Mode 2 (batched inline — Agent subagents not used)  
**Files Reviewed**: ~45 core paths (orchestration, retrieval, context, graph, MCP, models, tests)  
**HEAD**: `93fb2413958680398c132c7af1523364b6526a0f`  
**Load assumed**: Local-first MVP, one user, single API/worker process (README / QUICKSTART)

## Summary

| Severity | Count |
|----------|-------|
| Critical | 1 |
| Major    | 3 |
| Minor    | 2 |
| Info     | 2 |

## Architecture value (vs legacy)

| Area | Legacy | V2 | Value |
|------|--------|-----|-------|
| Routing | Text-line `TurnPrep`, verb heuristics | JSON `TurnPlan`, tiers 0/1/2, `writes_external` | **High** — fewer mistaken deep-agent runs |
| Retrieval | Single hybrid prefetch blob | RRF `EvidencePack`, parallel legs, 450ms budget | **High** — better fusion, typed citations |
| Context | Ad-hoc string concat + clip | `ContextCompiler` segment order + budgets | **High** — aligns with KV-cache / lost-in-middle mitigation |
| Grounding | Prompt-only | Deterministic `verify_grounded_answer` | **High** — closes prior review gap on invented filenames |
| Deep agent | One loop, many MCP tools | Consolidated v2 tools + subagents | **Medium–high** — isolation when flag on |
| Ops | Memory checkpointer | Postgres checkpointer + `turn_logs` / session state | **High** for durability; needs migrations |

**Verdict on strategy**: Flag-gated five-plane split is the right rollout pattern for this repo. Legacy default preserved; v2 is an opt-in upgrade, not a risky flip.

## Critical Issues

### [Release]: V2 schema and core modules not on branch HEAD
**File**: `backend/alembic/versions/20261120_0006_session_state_turn_log.py` (untracked); `backend/services/retrieval/core/pipeline.py` (untracked)  
**Smell**: Shotgun Surgery / incomplete tracer bullet

Alembic `0006`/`0007`, `pipeline.py`, `turn_plan_llm.py`, `compiler.py`, `verification/`, and related tests exist only in the working tree, not in `git show HEAD`. With `MINDOS_TURN_V2=1`, `orchestrate.py` calls `record_turn_log` on the same DB session as `append_message`; without migrated `turn_logs` / `chat_session_state` tables, the request transaction fails.

**Suggested Fix**: Commit all v2 modules + migrations; document `alembic upgrade head` in README (already present); CI or preflight check that v2 tables exist when flag is set.

## Major Issues

### [Performance]: Duplicate retrieval on v2 turns
**File**: `backend/services/agents/core/turn/orchestrate.py:274-295`  
**Smell**: Duplicate work

Every v2 turn runs full `prefetch_turn_retrieval` (hybrid + memories + mail lookup) and then `retrieve_evidence_pack` (hybrid again for up to 3 queries + memories + commitments). On a warm index this roughly doubles embedding/DB work and adds latency under the 450ms retrieval budget.

**Suggested Fix**: When `turn_v2` and tier-1 path: build `TurnRetrieval` from `EvidencePack` (or skip hybrid inside prefetch when plan queries are present). Keep live Gmail legs in prefetch only for tier-2 / mail-named attachments.

### [Reliability]: Evidence pipeline errors hidden
**File**: `backend/services/agents/core/turn/orchestrate.py:298-299`  
**Smell**: Silent failure

`retrieve_evidence_pack` failures are caught with bare `except Exception: evidence_pack = None`. Tier selection and verifier then behave as if retrieval succeeded empty vs failed, with no log line.

**Suggested Fix**: Log exception once; optionally set a `retrieval_failed` flag on metadata for turn_log.

### [Testing]: Unit suite not fully green
**File**: `tests/unit/test_attachment_extract.py`, `tests/unit/test_markdown_chat_lines.py`  
**Smell**: Broken Windows

`uv run pytest tests/unit` → **315 passed, 2 failed** (password-protected PDF extract, markdown blank lines). Unrelated to v2 but blocks “all green” CI narrative on the branch.

## Minor Issues

### [Observability]: `turn_log` misses retrieval timings
**File**: `backend/services/agents/core/turn/orchestrate.py:464-474`  
**Smell**: Incomplete tracing

`EvidencePack.timings_ms` and `rerank_margin` are not passed into `record_turn_log`, though the table has columns for them.

### [Process]: `record_turn_log` not isolated from session_state failures
**File**: `backend/services/agents/core/turn/orchestrate.py:451-502`  
**Smell**: Inconsistent error boundaries

`session_state` and `extract_memories_after_turn` are wrapped in `try/except`; `record_turn_log` is not. A logging insert failure should not roll back the assistant message (wrap or separate transaction).

## Info

- **Context optimization**: `ContextCompiler` segment order and evidence “best first, second best last” match context-optimization guidance; no further *required* token work for MVP.
- **Committed branch value** (without v2 flag): TurnPrep router, Gmail/Drive grounding, deep_session hardening (`MINDOS_PROJECT_ROOT`, no AGENTS.md leak) — solid incremental gains on `main`.

## Tests run

- `tests/unit` (full): 315 passed, 2 failed  
- Arch v2 focused: 35 passed (`test_turn_plan*`, `test_context_compiler`, `test_rrf`, `test_grounding_verifier`, `test_turn_routing`, `test_deep_session`, `test_gateway_limits`)

## Recommendations

1. Commit + migrate v2 artifacts before enabling `MINDOS_TURN_V2=1` in any shared environment.
2. Deduplicate v2 retrieval (Major #1) — highest ROI optimization.
3. Fix or quarantine the 2 failing unit tests before merge to `main`.
4. Legacy path (flag off): acceptable for production-style local MVP after test fixes.

## Context optimization note

No additional mandatory optimizations beyond retrieval deduplication. Compiler budgets and stable system prefix are production-appropriate for a single-user deployment.

---

## Backward compatibility (2026-11-20 addendum)

**Answer: backward compatibility was not removed.** `MINDOS_TURN_V2` defaults to `false` in `settings.py`. Legacy and v2 run side by side.

| Layer | Legacy (`flag off`) | V2 (`flag on`) |
|-------|---------------------|----------------|
| Orchestrator | `prepare_turn` → `TurnPrep`; `needs_deep_agent()` | `prepare_turn_plan` → tiers |
| Converse | Ad-hoc `Context:\n{ctx.context_text}` prompt | `ContextCompiler` + verifier |
| Deep agent | Preload stuffed into **human** message | Evidence on **ephemeral system** prompt + subagents |
| MCP personal | 12 `mindos_*` tools incl. sync/outbox | 7 consolidated tools |
| Post-turn | Messages only | `turn_log`, session state, memory extract |

**Internal bridge (not end-user legacy):** `turn_prep_from_plan` / `turn_plan_bridge` still map `TurnPlan` ↔ `TurnPrep` so prefetch helpers work — keep or refactor prefetch to take `TurnPlan` directly when deleting compat.

### v2-only migration checklist (no code removed yet)

1. Commit untracked v2 modules + Alembic `0006`/`0007`; run migrations.
2. Set default `mindos_turn_v2=True` or delete the setting and branches.
3. Remove: `orchestrate` `else` branch (`prepare_turn`, `needs_deep_agent`); `converse_synth` non-v2 prompt path; `personal_tools` `legacy` list; `deep_agent` `else` preload-in-user-message block (~lines 401–429).
4. Collapse retrieval: one plane (`retrieve_evidence_pack` + mail live legs), not prefetch hybrid + pipeline.
5. Update tests (`test_turn_routing`, tool tests) and `mcp_servers.json` guidance (drop “Legacy:” paragraph).
6. Optional later: drop text-line `prepare_turn` LLM in `turn_prep.py` if `turn_plan_llm` fully replaces it; keep `fast_path_turn_prep` for cheap paths.

### Context win from v2-only

Dropping the legacy deep path removes large unstable **user-message prefixes** (preload + history) and shrinks the tool-definition prefix — aligns with KV-cache / observation-mask goals without extra machinery.