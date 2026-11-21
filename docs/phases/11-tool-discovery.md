# 11 · Dynamic Tool Discovery (gated)

Maps to ARCHITECTURE §4.2 stages 2–3. **Start only if measurement says always-loaded sets hurt.**

## Skills
| Skill | Source |
|---|---|
| `tool-design` | local |
| `langchain-eval-engineering` | local |
| `context-optimization` | local |

## Entry criterion
Tool-selection error rate or schema token cost, measured in Phase 07 evals, exceeds threshold set in an ADR.

## Build
- Registry index (keyword + embedding over summaries); `search_tools(query, namespace?)`, `load_tools(names)`.
- Visibility filtered by principal + namespace; middleware rebinds tools per step.

## Gate
- A/B eval: measurable gain in accuracy or tokens. Else delete the phase.
- `search_tools` cannot reveal or load non-granted tools (test).
