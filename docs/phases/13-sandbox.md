# 13 · Sandboxed Programmatic Execution (only with evidence)

Maps to ARCHITECTURE §4.4, §5.6. **Requires an ADR with measured bulk-workflow need.**

## Skills
| Skill | Source |
|---|---|
| `security-setup` | local |
| `llm-security` | `semgrep/skills` (1.8K) |
| `code-review` | local |
| `python-testing-patterns` | `wshobson/agents` |

## Entry criterion
Real workflows (e.g. 400-thread relabel) are too slow, costly, or error-prone via tool calls. Evidence attached.

## Build
- Backend ADR (container vs OS sandbox). Separate process, no secrets, no network, scratch FS, resource caps.
- MindOS SDK stubs → length-prefixed JSON over Unix socket; frame size, rate, ordering limits.
- Every call re-enters the engine as principal `SANDBOX`; reads immediate, effects deferred into operation set.
- Batch approval (Phase 05 tables): per-item hash, approve subset, R4 typed count.

## Gate
- Escape/limit tests; no secrets in env; unknown tool rejected; lost ack on effect is reconciled, not resent.
