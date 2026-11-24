"""Virtualized transcript (ADR 0002): one ScrollView that renders only the visible lines."""

from __future__ import annotations

from bisect import bisect_right

from rich.cells import cell_len
from rich.style import Style
from textual.events import Resize
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.selection import Selection
from textual.strip import Strip

from mindos.presentation.render import RowRenderer
from mindos.presentation.viewmodel import Row


class Transcript(ScrollView, can_focus=False):
    """Renders cached per-row lines. Cost per frame does not grow with history."""

    def __init__(self, *, plain: bool = False, id: str | None = None) -> None:
        super().__init__(id=id)
        self._plain = plain
        self._renderer = RowRenderer(80, plain=plain)
        self._flags: tuple[bool, bool] = (False, True)
        self._rows: tuple[Row, ...] = ()
        self._lines: list[list[Strip]] = []  # rendered lines per row
        self._starts: list[int] = []  # first line index of each row
        self._total = 0
        self._last_height = 0
        self._cache: dict[str, tuple[Row, list[Strip]]] = {}

    # ---- state -----------------------------------------------------------------------------
    @property
    def total_lines(self) -> int:
        return self._total

    @property
    def at_tail(self) -> bool:
        return self.scroll_y >= self.max_scroll_y - 1

    def set_view(self, rows: tuple[Row, ...], *, expanded: bool, todos_visible: bool) -> None:
        """Show `rows`. Re-renders only rows from the first one that changed."""
        flags = (expanded, todos_visible)
        if flags != self._flags:
            self._flags = flags
            self._reset()
        was_tail = self.at_tail
        old = self._rows
        first = 0
        limit = min(len(old), len(rows))
        while first < limit and old[first] is rows[first]:
            first += 1
        del self._lines[first:]
        del self._starts[first:]
        pos = self._starts[-1] + len(self._lines[-1]) if self._lines else 0
        for row in rows[first:]:
            lines = self._render_row(row)
            self._starts.append(pos)
            self._lines.append(lines)
            pos += len(lines)
        self._rows = rows
        self._total = pos
        self.virtual_size = Size(self.size.width, pos)
        if was_tail:
            # max_scroll_y updates after layout, so scroll once the new size is in effect.
            self.call_after_refresh(self.jump_to_tail)
        self.refresh()

    def jump_to_tail(self) -> None:
        self.scroll_to(y=self.max_scroll_y, animate=False, immediate=True)

    # ---- selection (drag to select, Ctrl+C or release to copy) -------------------------------
    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """The selected text. Offsets are (column, line index), set in `render_line`."""
        text = "\n".join(strip.text.rstrip() for lines in self._lines for strip in lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self.refresh()

    def _highlight(self, strip: Strip, span: tuple[int, int]) -> Strip:
        start, end = span
        text = strip.text
        first = cell_len(text[:start])
        last = strip.cell_length if end == -1 else cell_len(text[:end])
        if last <= first:
            return strip
        style = self.screen.get_component_rich_style("screen--selection")
        return Strip.join(
            [strip.crop(0, first), strip.crop(first, last).apply_style(style), strip.crop(last)]
        )

    # ---- rendering -------------------------------------------------------------------------
    def render_line(self, y: int) -> Strip:
        _, offset = self.scroll_offset
        index = y + offset
        width = self.size.width
        if index >= self._total:
            return Strip.blank(width, self.rich_style)
        row = bisect_right(self._starts, index) - 1
        strip = self._lines[row][index - self._starts[row]]
        if strip.cell_length != width:
            strip = strip.adjust_cell_length(width, Style.null())
        selection = self.text_selection
        if selection is not None and (span := selection.get_span(index)) is not None:
            strip = self._highlight(strip, span)
        return strip.apply_offsets(0, index)  # lets Textual map the mouse to text

    def on_resize(self, event: Resize) -> None:
        # Judge "was following the tail" by the old height: a shrinking pane (an approval
        # dialog opening) must keep the newest line visible.
        was_tail = self.scroll_y >= max(0, self._total - self._last_height) - 1
        self._last_height = event.size.height
        if was_tail:
            self.call_after_refresh(self.jump_to_tail)
        if event.size.width != self._renderer.width:
            self._renderer = RowRenderer(event.size.width, plain=self._plain)
            rows = self._rows
            self._reset()
            self.set_view(rows, expanded=self._flags[0], todos_visible=self._flags[1])

    def _reset(self) -> None:
        self._rows = ()
        self._lines = []
        self._starts = []
        self._cache = {}

    def _render_row(self, row: Row) -> list[Strip]:
        hit = self._cache.get(row.id)
        if hit is not None and (hit[0] is row or hit[0] == row):
            return hit[1]
        lines = self._renderer.render(row, expanded=self._flags[0], todos_visible=self._flags[1])
        self._cache[row.id] = (row, lines)
        return lines
