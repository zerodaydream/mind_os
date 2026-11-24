"""Rows to cached terminal lines (ADR 0002).

The transcript never mounts a widget per block. Each row is rendered once by Rich into a list of
`Strip` lines, cached, and re-rendered only when the row changes or the width changes.
"""

from __future__ import annotations

import io

from rich import box
from rich.console import Console, ConsoleRenderable, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual.strip import Strip

from mindos.presentation.tokens import PALETTE, Glyphs, Palette
from mindos.presentation.viewmodel import (
    AssistantRow,
    CommandRow,
    NoticeRow,
    Row,
    SubagentRow,
    ThinkingRow,
    TodosRow,
    ToolRow,
    UserRow,
    WelcomeRow,
)

COLLAPSED_RESULT_LINES = 3
_BLANK_AFTER = (UserRow, AssistantRow, WelcomeRow, TodosRow, NoticeRow)


def _with_styles(line: list[Segment]) -> list[Segment]:
    """Rich leaves padding segments unstyled; Textual's color filters need a style on each."""
    null = Style.null()
    return [s if s.style is not None else Segment(s.text, null, s.control) for s in line]


class RowRenderer:
    def __init__(
        self,
        width: int,
        *,
        plain: bool = False,
        palette: Palette = PALETTE,
    ) -> None:
        self.width = max(width, 20)
        self._glyphs = Glyphs.ascii() if plain else Glyphs()
        self._p = palette
        self._console = Console(
            width=self.width,
            file=io.StringIO(),
            force_terminal=not plain,
            color_system=None if plain else "truecolor",
            no_color=plain,
            legacy_windows=False,
        )

    # ---- public ----------------------------------------------------------------------------
    def render(self, row: Row, *, expanded: bool, todos_visible: bool) -> list[Strip]:
        renderable = self._build(row, expanded, todos_visible)
        if renderable is None:
            return []
        options = self._console.options.update(width=self.width, height=None)
        lines = self._console.render_lines(renderable, options, pad=True)
        strips = [Strip(_with_styles(line), self.width) for line in lines]
        if isinstance(row, _BLANK_AFTER):
            strips.append(Strip.blank(self.width, Style.null()))
        return strips

    # ---- builders --------------------------------------------------------------------------
    def _build(self, row: Row, expanded: bool, todos_visible: bool) -> ConsoleRenderable | None:
        match row:
            case WelcomeRow():
                return self._welcome(row)
            case UserRow():
                return self._user(row)
            case AssistantRow():
                return self._assistant(row)
            case ThinkingRow():
                return self._thinking(row, expanded)
            case ToolRow():
                return self._tool(row, expanded)
            case SubagentRow():
                return self._subagent(row)
            case TodosRow():
                return self._todos(row) if todos_visible else None
            case NoticeRow():
                return self._notice(row)
            case CommandRow():
                return self._result_block(row.text, Style(color=self._p.dim), expanded=True)
        return None

    def _grid(self, lead: str, body: ConsoleRenderable, lead_style: Style) -> Table:
        """A hanging-indent layout: a fixed-width lead column and a wrapping body column."""
        grid = Table.grid(padding=0, expand=True)
        grid.add_column(width=len(lead), no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(lead, style=lead_style), body)
        return grid

    def _welcome(self, row: WelcomeRow) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        lines = [
            Text.assemble(
                (f"{g.thinking} MindOS", Style(color=p.accent, bold=True)),
                (f"  v{row.version}", Style(color=p.dim)),
            ),
            Text(f"cwd  {row.cwd}", style=Style(color=p.dim)),
        ]
        if row.models:
            lines.append(Text("models  " + " · ".join(row.models), style=Style(color=p.dim)))
        lines.append(Text(row.tip, style=Style(color=p.dim)))
        border = Style(color=p.accent)
        return Panel(
            Group(*lines),
            box=box.ROUNDED if self._glyphs.bullet != "*" else box.ASCII,
            border_style=border,
            padding=(0, 1),
            expand=False,
        )

    def _user(self, row: UserRow) -> ConsoleRenderable:
        p = self._p
        bg = Style(bgcolor=p.user_bg, color=p.text)
        body = Text(row.text, style=bg, overflow="fold")
        grid = Table.grid(padding=0, expand=True)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(
            Text(f"{self._glyphs.prompt} ", style=Style(bgcolor=p.user_bg, color=p.dim)), body
        )
        grid.style = bg
        return grid

    def _assistant(self, row: AssistantRow) -> ConsoleRenderable:
        body: ConsoleRenderable = Markdown(row.text or " ", code_theme="monokai")
        return self._grid(f"{self._glyphs.bullet} ", body, Style(color=self._p.text))

    def _thinking(self, row: ThinkingRow, expanded: bool) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        dim = Style(color=p.dim, italic=True)
        if row.done:
            tokens = f" ({row.tokens} tokens)" if row.tokens else ""
            title = f"{g.thinking} Thought{tokens}"
        else:
            title = f"{g.thinking} Thinking{g.ellipsis}"
        if not expanded:
            hint = " (ctrl+o to expand)" if row.text else ""
            return Text(title + hint, style=dim)
        return Group(Text(title, style=dim), Padding(Text(row.text, style=dim), (0, 0, 0, 2)))

    def _tool(self, row: ToolRow, expanded: bool) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        color = {"running": p.accent, "ok": p.ok, "error": p.error, "rejected": p.warn}[row.status]
        head = Text.assemble(
            (row.tool, Style(bold=True, color=p.text)),
            (f"({row.args})" if row.args else "()", Style(color=p.dim)),
        )
        parts: list[ConsoleRenderable] = [self._grid(f"{g.bullet} ", head, Style(color=color))]
        if row.status == "running" and not row.result:
            parts.append(self._result_block(f"Running{g.ellipsis}", Style(color=p.dim), True))
        elif row.result:
            style = Style(color=p.error if row.status == "error" else p.dim)
            parts.append(self._result_block(row.result, style, expanded))
        group = Group(*parts)
        return Padding(group, (0, 0, 0, 2 * row.indent)) if row.indent else group

    def _result_block(self, text: str, style: Style, expanded: bool) -> ConsoleRenderable:
        g = self._glyphs
        lines = text.splitlines() or [""]
        if not expanded and len(lines) > COLLAPSED_RESULT_LINES:
            hidden = len(lines) - COLLAPSED_RESULT_LINES
            lines = [
                *lines[:COLLAPSED_RESULT_LINES],
                f"{g.ellipsis} +{hidden} lines (ctrl+o to expand)",
            ]
        body = Text("\n".join(lines), style=style, overflow="fold")
        return self._grid(f"  {g.result}  ", body, Style(color=self._p.dim))

    def _subagent(self, row: SubagentRow) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        head = Text.assemble(
            (f"Subagent {row.name}", Style(bold=True, color=p.text)),
            (f": {row.brief}" if row.brief else "", Style(color=p.dim)),
        )
        color = {
            "running": p.accent,
            "success": p.ok,
            "partial": p.warn,
            "failed": p.error,
            "needs_input": p.warn,
        }[row.status]
        parts: list[ConsoleRenderable] = [self._grid(f"{g.bullet} ", head, Style(color=color))]
        if row.summary:
            parts.append(self._result_block(row.summary, Style(color=p.dim), True))
        return Group(*parts)

    def _todos(self, row: TodosRow) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        done = sum(1 for t in row.todos if t.status == "done")
        head = Text(f"Plan ({done}/{len(row.todos)} done)", style=Style(bold=True, color=p.text))
        body = Text()
        for i, todo in enumerate(row.todos):
            mark = {"pending": g.todo_open, "in_progress": g.todo_active, "done": g.todo_done}[
                todo.status
            ]
            style = Style(color=p.dim, strike=todo.status == "done")
            body.append(f"{mark} {todo.text}", style=style)
            if i < len(row.todos) - 1:
                body.append("\n")
        return Group(
            self._grid(f"{g.bullet} ", head, Style(color=p.accent)),
            self._grid(f"  {g.result}  ", body, Style(color=p.dim)),
        )

    def _notice(self, row: NoticeRow) -> ConsoleRenderable:
        p, g = self._p, self._glyphs
        glyph, color = {
            "info": (g.info, p.accent),
            "warning": (g.warn, p.warn),
            "error": (g.fail, p.error),
            "dim": (g.result, p.dim),
        }[row.level]
        body = Text(row.text, style=Style(color=color if row.level != "info" else p.text))
        return self._grid(f"{glyph} ", body, Style(color=color))
