# Code review — Gmail grounding + router reasoning

**Commit base:** `2e1e25ce004f606d4aa3b6c97f4ae299e0608ebd`  
**Scope:** Working tree (Gmail/Drive ingest, turn context, CLI links, router LLM)  
**Mode:** Inline small diff

## Executive summary

| Question | Verdict |
|----------|---------|
| Does a reasoning model improve decisions? | **Partially yes** — better for turn routing (plan/execute vs converse, TASK merging) and multi-step tool planning; **does not** fix invented attachment names (needs metadata + prompts). |
| Cheapest Google reasoning | **`gemini-2.5-flash`** with a capped `thinking_budget` (implemented for router only). |

## Critical

None in reviewed diff.

## Major

1. **`_mail_followup_topic` (turn_context.py)** — Merging any short follow-up after an assistant reply can attach the wrong prior topic if the user pivots in one line. **Mitigation:** user clarification or tighten heuristic (length/token overlap) later.

2. **Invented filenames** — Still model-side; grounding rules in `deep_agent.py` + `Drive ·` lines reduce risk but are not enforced in code. **Mitigation:** post-process or reject citations not in metadata (not implemented).

3. **Stale Drive IDs after `mindos cleanup`** — `refresh_gmail_message_drive` + `drive_file_exists` help; DB rows without re-ingest can still 404 until refresh runs.

## Minor

1. **`EXTRACT_MAX_OUTPUT_TOKENS = 32`** for router — enough for INTENT/TASK lines; increase if TASK merges grow.

2. **Router reasoning cost** — Every `needs_turn_routing` call pays thinking tokens; budget default 1024 caps cost vs `thinking_budget=-1`.

## Info

- Removed hardcoded recipient names — good.
- URL rendering / no OSC 8 — good for TUI copy.

## Implementation added (router reasoning)

- `LlmTask.ROUTER` → `MINDOS_ROUTER_MODEL` (default `gemini-2.5-flash`) + `MINDOS_ROUTER_THINKING_BUDGET`.
- `google_generate_config()` passes `thinking_config` to Gemini for router completions only.
- Deep agent / synth unchanged (`MINDOS_SYNTH_MODEL`).

## Tests run

- `tests/unit/test_google_router.py`
- `tests/unit/test_mail_followup_context.py`
- `tests/unit/test_llm_gateway.py` (existing)

## Sampling

Full tree not scanned; focus on mail/chat/LLM paths from user thread.