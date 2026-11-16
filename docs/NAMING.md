# Python naming (mind_os)

Follow [PEP 8](https://peps.python.org/pep-0008/#naming-conventions) and the [Google Python Style Guide — Naming](https://google.github.io/styleguide/pyguide.html#s3.16-naming). Names are documentation; abbreviations and opaque flags (`_ready`, `_running`) are reserved only for truly local loop variables.

## Public vs module-private

| Scope | Pattern | Example |
|-------|---------|---------|
| Public API (imported elsewhere) | no leading `_`, full words | `start_shared_event_loop`, `start_chat_warmup` |
| Module-private | one leading `_`, still descriptive | `_chat_warmup_result`, `_shared_event_loop` |
| “Internal but tested” | same as private; tests may touch `_` symbols | `_take_chat_warmup_result` |

Do **not** export vague names (`prefetch`, `boot`, `repl_running`) without a domain noun (`chat`, `event_loop`, `session`).

## Functions and methods

- **Verbs** for actions: `prepare_*`, `load_*`, `resolve_*`, `consume_*`, `format_*`.
- **Predicates** for booleans: `is_*`, `has_*`, `can_*` — `shared_event_loop_is_running()`, not `repl_running()`.
- **Pairs** share a noun stem: `start_shared_event_loop` / `stop_shared_event_loop`.
- One leading `_` only for helpers not meant as package API (e.g. `cli/chat_cmd.prepare_chat_session_state`).

## Variables and module state

- Booleans: `chat_owns_event_loop`, not `own_repl`.
- Locks/threads/cache: name the **resource**, not the mechanism: `_chat_warmup_thread`, not `_prefetch_thread`.
- Avoid shortened state: `_ready` → `_chat_warmup_result` (what it holds).
- Module globals: `_action_output_lines`, `_curses_main_window` — not `_sink`, `_tui_stdscr`.

## Constants

- `SCREAMING_SNAKE` for true constants: `_CHAT_LOADING_LABELS`.
- Config-like tuples at module level: same rule.

## CLI / TUI (`cli/term.py`)

Large files may keep `_` prefixes on drawing helpers, but each name should still say **what** it does: `_menu_draw`, `_chat_save_term`. Prefer `_session_menu_draw` over `_menu_draw` when multiple menus exist. Refactor opportunistically when you touch a function.

## Commands entrypoints

- User-facing commands: `cmd_<noun>` — `cmd_chat`, `cmd_status`.
- REPL driver: `run_repl`.

## When renaming

1. Grep callers (including tests) before editing.
2. Rename definition + all references in one change.
3. Add or update a one-line docstring on non-obvious module globals.

New code should match these rules; legacy `backend/` names can be cleaned when that code is edited.