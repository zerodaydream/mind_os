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
Source: official public docs, `code.claude.com/docs/en/interactive-mode` (read 2026-11-22). Not from leaked source.

| Key | Behavior |
|---|---|
| `Enter` | Submit |
| `Shift+Enter`, `Option+Enter`, `\`+`Enter` | Newline |
| `↑/↓` | Move cursor in multiline input first; at first/last row, navigate history |
| `Ctrl+R` | Reverse history search |
| `Esc` | Interrupt run (keeps work done). Closes dialog. On approval dialog: decline (= No) |
| `Esc Esc` | Text in input: clear and save draft to history. Empty input: open rewind menu |
| `Ctrl+C` | Interrupt; if idle, clear input; second press exits |
| `Ctrl+D` | Exit (confirm within 800 ms); deletes char if input has text |
| `Ctrl+O` | Transcript viewer (tool details, timestamps, model per message). `[` dumps to scrollback; `/` searches |
| `Ctrl+T` | Toggle todo checklist |
| `Ctrl+L` | Full redraw |
| `Ctrl+S` | Stash / restore prompt |
| `Ctrl+G` | Edit prompt in `$EDITOR` |
| `Shift+Tab` | Cycle approval mode (our modes: `default`, `plan`, `acceptEdits`-equivalent only where policy allows; never loosens `policy.toml`) |
| `Tab` | Accept autocomplete; on approval dialog, open comment field on focused option |
| `Option+P` | Switch model |
| `Option+T` | Toggle thinking (our `/think` level cycle) |
| `Ctrl+A/E/K/U/W/Y`, `Alt+B/F/D` | Readline editing |
| `/` at start | Slash menu |
| `@` | Path / entity autocomplete |
| `?` on empty input | Shortcuts panel |
| `Ctrl+B` | Background the running task (needs Phase 08 worker) |

**Excluded on purpose:** `!` shell mode (no shell tool, §14), bypass-permissions mode (violates MISSION principle 3).

## Slash commands (UI-side)
`/help /clear /resume /model /think /status /doctor /memory /policy /cost /compact /config /exit`. `/think <level>` sets reasoning effort for the session (cost and latency only). Status line shows the current level and reasoning tokens. Output as `CommandOutput` events.

## Engineering
- `AppEvent → reducer → immutable view-model → widgets`. Widgets call only application commands.
- **Transcript = one virtualized line-rendered `ScrollView` (ADR 0002).** Do not mount a widget per block. Cache Rich renderables per block as lines; re-render only the streaming block and on width change. Selection/copy implemented in the view.
- Coalesce text deltas at 30–60 Hz.
- Tokens in `theme/tokens.tcss`; no hard-coded colors.
- Resize reflow, min-size fallback, 256/16-color degradation.
- Fake agent: JSONL scenario replayer (streaming, parallel tools, approval, error, long output).

## Gate
- Pilot tests per widget + 5 scripted scenarios; side-by-side match vs reference captures (Phase 01).
- 50k-line transcript: streaming flush p95 < 50 ms (measured baseline 44 ms); no flicker on stream.
- Every action reachable by keyboard.

## Known limit
Native-scrollback "classic" mode is not reproducible in Textual. Provide `[` dump-to-scrollback instead.

## Status (built 2026-11-22)
Built and passing the gate against the scripted fake agent (`uv run ops --demo`; type `stream`, `tools`, `approval`, `error`, or `long`). The real agent connects in Phase 3 through the same `AgentRuntime` port.

Done: welcome block, virtualized transcript with cached per-row lines and sticky tail, markdown and code rendering, tool and subagent rows, plan list, thinking rows, collapsible long output, spinner, multiline prompt with history, slash menu with Tab completion, hint and status lines, approval panel with edit and comment fields, interrupt and exit keys, `Shift+Tab` mode cycle, effort cycle, stash, `?` panel, resize reflow, NO_COLOR and ASCII mode. Slash commands `/help /clear /exit /think /status /cost /model` work; the rest report "not available yet".

Gate results: 50k-line transcript streaming flush p95 under 50 ms (test `test_50k_line_transcript_streaming_flush_under_50ms`), full flush path including status line and hint.

Deferred, each with an owner:
- Side-by-side visual match against reference captures: needs captures in `docs/ui-reference/` (none supplied).
- Diff view, mouse text selection and copy, find in transcript, `[` dump to scrollback, `Ctrl+O` full transcript viewer: next TUI polish pass.
- `Ctrl+R` history search, `Ctrl+G` external editor, `@` path autocomplete, `Option+P` model switch, `Ctrl+B` background task, `Esc Esc` rewind: need Phase 3 (rewind, models), Phase 7 (background), Phase 8 (paths).
- Collapsing a subagent block, integrations and jobs counts in the status line, R4 typed-count confirmation: need Phases 5, 7 and the policy engine.
- `/think` changes the displayed level only until the agent runtime reads it in Phase 3.
