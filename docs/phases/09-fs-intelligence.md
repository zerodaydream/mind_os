# 09 · Filesystem Intelligence

Maps to ARCHITECTURE §8.

## Skills
| Skill | Source |
|---|---|
| `filesystem-context` | local |
| `postgresql-optimization` | `github/awesome-copilot` (17K) |
| `postgresql-table-design` | `wshobson/agents` |
| `memory-systems` | local (hybrid retrieval ideas) |
| `code-optimizer` | local |
| `test-coverage` | local |

## Build (in order, benchmark after each)
1. `FileSearch` port + tier 0 (`mdfind`) + tier 2 (`rg`, argv only, no shell).
2. Tier 1 `pg_trgm` + `files` table.
3. Indexer pipeline on jobs: eligibility → change detect → extract → chunk → upsert (one txn/file). Startup reconciliation scan.
4. FSEvents watcher (library ADR), debounced.
5. Tier 3 FTS (`tsvector`).
6. Tier 4 `pgvector`; embedding cache keyed `(chunk_hash, model_id, dims)`; HNSW vs IVFFlat **measured**.
7. RRF fusion (`k` configurable) + boosts + dedupe; query-shape router.
8. Freshness check (`stat`) + permission recheck on every hit.

## Gate
- Labeled query sets: precision@k, recall@k, MRR per tier and fused.
- Denylisted file never indexed nor returned (ingest + query).
- Stale-hit test; poison-file quarantine.
