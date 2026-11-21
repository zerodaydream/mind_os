# 12 · MCP Integrations

Maps to ARCHITECTURE §4.1, §7.

## Skills
| Skill | Source |
|---|---|
| `mcp-builder` | `anthropics/skills` (145K) |
| `MCP Integration` | `anthropics/claude-code` (15.6K) |
| `llm-security` | `semgrep/skills` (1.8K) |
| `tool-design` | local |

## Build
- `mcp.toml` server allowlist; stdio servers with minimal env; version-pinned schemas re-validated at startup.
- Adapter into registry; default risk **R3** until user classifies; descriptions and outputs untrusted (taint `mcp`).
- Per-server namespace and subagent grant; model cannot register tools.
- TUI: MCP tools shown as `server.tool` rows; permission dialog `McpTool` variant.

## Gate
- Unlisted server rejected. Schema drift blocks tool. Tainted MCP output cannot trigger unapproved send.
