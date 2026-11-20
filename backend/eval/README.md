# MindOS eval

Phase 0/2 seed. Expand when the Phase 1 retrieval branch merges.

## Datasets

| File | Purpose |
|------|---------|
| `datasets/routing_smoke.jsonl` | Tier 0/1/2 routing smoke cases |

## Run (unit stand-in)

```bash
uv run pytest tests/unit/test_turn_routing.py tests/unit/test_turn_plan.py -q
```

A full `mindos eval` CLI lands with Phase 1 (retrieval metrics + turn_log gates).
