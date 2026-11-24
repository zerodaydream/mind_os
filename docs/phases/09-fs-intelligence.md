# 09 · File Search (multi-root, live-first)

Maps to ARCHITECTURE §8. Model: Claude Code's live scan as default, Cursor's index as a per-root accelerator, semantic for meaning.

## Skills
| Skill | Source |
|---|---|
| `filesystem-context` | local |
| `postgresql-optimization` | `github/awesome-copilot` (17K) |
| `postgresql-table-design` | `wshobson/agents` |
| `memory-systems` | local |
| `code-optimizer` | local |
| `test-coverage` | local |

## Measured baseline (this Mac, Nov 2026)
| Query | Time |
|---|---|
| `rg` Desktop+Downloads+Documents, default ignore | 0.66 s |
| `rg` Desktop, `--no-ignore --hidden` | 6.6 s (scans `node_modules`, caches) |
| `mdfind` name, home folder | ~1.0 s |
| `mdfind` content, home folder | ~1.2 s |
Re-measure in this phase; these set the thresholds.

## Build (in order, benchmark after each)
1. **Root registry**: `mindos roots add|remove|list`; per-root state `LIVE | INDEXED`; per-root exclusion rules (`node_modules`, `.venv`, `.git`, caches, `Library`, build output) plus the global denylist.
2. **Tier 0**: `mdfind -onlyin <root>` for names and document content. `mindos doctor` checks `mdutil -s` per volume.
3. **Tier 1**: `rg` live, argv only, no shell, scoped, parallel across roots under one latency budget.
4. **Router + fusion** (RRF) across tiers 0/1; freshness `stat` and permission recheck on every hit.
5. **Latency log** per root; **promotion rule**: live p95 > threshold (default 1.5 s) ⇒ root becomes `INDEXED`.
6. **Tier 2 accelerator** (jobs from Phase 08): extract text → `files` + GIN `gin_trgm_ops` on content. Query = trigram candidates → live `rg` verify on candidates only. Patterns with no extractable trigrams go straight to live `rg`.
7. **Watcher + startup reconciliation** (only for `INDEXED` roots).
8. **Tier 3 semantic**: chunk → embed (cache by `(chunk_hash, model_id, dims)`) → `pgvector`; HNSW vs IVFFlat measured. Covers PDFs/Office that grep cannot read.

## Gate
- p95 < 1 s for name and exact search on registered roots (live or indexed).
- Indexed hit is always live-verified; stale hit test.
- Denylisted file never indexed nor returned (ingest + query).
- Labeled query sets: precision@k, recall@k, MRR for semantic and fused.
- Promotion/demotion test with a synthetic large root.

## Don't
- No whole-disk scan. No FTS tier (removed). No index on roots that are fast live.
