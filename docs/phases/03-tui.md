# 03 · TUI (Claude Code UI, fullscreen mode)

Maps to ARCHITECTURE §13. Built against a **scripted fake event stream**; no real agent needed.
Same layout and behavior as Claude Code's fullscreen renderer; own name, palette, assets. No copied code.

## Skills
| Skill | Source |
|---|---|
| `tui-design` | `npx skills add hyperb1iss/hyperskills@tui-design` (1.4K) |
| `textual-tui-skill` | `podo/design-agent-skills` (278 installs, low: review before use) |
| `frontend-design`, `dont-make-me-think`, `ux-ax-review` | local → polish + review |
| `user-test` | local → drive the app |
| `python-testing-patterns` | Textual `Pilot` tests |

## Layout (top → bottom)
1. **Welcome block** (rounded border): app mark, version, cwd, tip line.
2. **Transcript** (scrollable, sticky tail, "jump to latest").
3. **Spinner line** while running.
4. **Prompt box** (rounded border, `>` prompt, multiline).
5. **Hint/mode line** under prompt: `? for shortcuts` or mode label + `shift+tab to cycle`.
6. **Status line**: model tier · integrations · jobs · pending approvals · context % · cost.

## Transcript rows
| Row | Rendering |
|---|---|
| User | `> text`, subtle background |
| Assistant text | `⏺ ` bullet, streamed Markdown, syntax-highlighted code |
| Tool call | `⏺ namespace.tool(args preview)` |
| Tool result | `  ⎿  summary` ; long → `… +N lines (ctrl+o to expand)` |
| Thinking | `✻ Thinking…` collapsed, expand with ctrl+o |
| Todos | `☐ / ☒` list under `⎿` |
| Subagent | grouped, collapsible block with own tool rows |
| Error / notice | red / dim, glyph-differentiated (not color only) |
| Diff | unified + split toggle, paged |

## Spinner
`✻ Verb… (12s · ↓ 1.2k tokens · esc to interrupt)`. Glyph cycles (`· ✢ ✳ ✶ ✻ ✽`), shimmer on verb, elapsed after 2s. Honor `NO_COLOR`/reduced motion; ASCII fallback set.

## Permission dialog (approval panel)
Bordered, blocking, focus on **safe** option. Title, resolved targets, preview/diff, risk badge, consequence text, expiry.
Options (arrows + Enter, or number keys):
1. Yes (`a`)  2. Yes + edit (`e`)  3. No, tell agent what to do instead (`r`, `esc`).
"Always allow" only appears if policy permits (Phase 05). R4: itemized list + typed count.

## Input & keys
`Enter` submit · `Shift+Enter` / `\`+Enter newline · `↑/↓` history · `Ctrl+R` history search · `Tab` / `@` path & entity completion · `/` slash menu · `Shift+Tab` cycle mode · `Esc` cancel run (twice = force; on empty input twice = rewind picker) · `Ctrl+C` clear / twice exit · `Ctrl+O` transcript mode (`/` search, `[` dump to scrollback) · `?` shortcuts overlay.
No `!` shell mode (no shell tool by design, §14).

## Slash commands (UI-side)
`/help /clear /resume /model /status /doctor /memory /policy /cost /compact /config /exit`. Output as `CommandOutput` events.

## Engineering
- `AppEvent → reducer → immutable view-model → widgets`. Widgets call only application commands.
- Coalesce text deltas at 30–60 Hz; re-layout only active block; virtualize + collapse finished blocks.
- Tokens in `theme/tokens.tcss`; no hard-coded colors.
- Resize reflow, min-size fallback, 256/16-color degradation.
- Fake agent: JSONL scenario replayer (streaming, parallel tools, approval, error, long output).

## Gate
- Pilot tests per widget + 5 scripted scenarios; side-by-side match vs reference captures (Phase 01).
- 5k-block transcript scrolls without lag; no flicker on stream.
- Every action reachable by keyboard.

## Known limit
Native-scrollback "classic" mode is not reproducible in Textual. Provide `[` dump-to-scrollback instead.
