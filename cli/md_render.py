"""Lightweight markdown → terminal layout (tables as ASCII grids)."""

from __future__ import annotations

import re
import textwrap

_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
_NOTE = re.compile(r"^\*\((.+)\)\*\s*$")
_LIST = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")


def strip_inline_md(text: str) -> str:
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    return text.strip()


def display_for_tui(line: str, style: str) -> str:
    """Strip markdown syntax; curses shows style via attributes, not glyphs."""
    raw = line.rstrip()
    if not raw.strip():
        return ""
    if style == "list":
        m = _LIST.match(raw)
        if m:
            return f"{m.group(1)}{m.group(2)} {strip_inline_md(m.group(3))}"
    if style == "source_item":
        body = raw.strip()
        if re.match(r"^\d+\.", body):
            return f"  {strip_inline_md(body)}"
        return f"  {strip_inline_md(body)}"
    s = raw.strip()
    if style == "heading":
        m = _HEADING.match(s)
        if m:
            return strip_inline_md(m.group(1))
    if style == "meta":
        s = s.strip("_")
        m = _NOTE.match(s)
        if m:
            return m.group(1).strip()
    return strip_inline_md(s)


def is_table_line(line: str) -> bool:
    return line.strip().count("|") >= 2


def is_separator_line(line: str) -> bool:
    t = line.strip()
    if not t or not is_table_line(t):
        return False
    cells = split_cells(t)
    if not cells:
        return False
    for cell in cells:
        core = cell.strip().replace(":", "").strip()
        if not core or not set(core) <= {"-"}:
            return False
    return True


def split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [strip_inline_md(c) for c in s.split("|")]


def parse_pipe_table(block: list[str]) -> list[list[str]]:
    rows: list[list[str]] = []
    for ln in block:
        if is_separator_line(ln):
            continue
        cells = split_cells(ln)
        if cells:
            rows.append(cells)
    return rows


def ascii_table(rows: list[list[str]], width: int) -> list[str]:
    if not rows:
        return []
    width = max(24, width)
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    col_w = [max(len(rows[r][c]) for r in range(len(rows))) for c in range(ncol)]
    col_w = [max(1, w) for w in col_w]

    def _total() -> int:
        return sum(col_w) + 3 * ncol + 1

    # shortcut: min 6 chars per column; wrap tall cells instead of truncating to 1 char
    _MIN_COL = 6
    while _total() > width and max(col_w) > _MIN_COL:
        c = max(range(ncol), key=lambda i: col_w[i])
        col_w[c] -= 1

    def _border() -> str:
        return "+" + "+".join("-" * (w + 2) for w in col_w) + "+"

    def _row(cells: list[str]) -> str:
        parts = []
        for c in range(ncol):
            txt = (cells[c] if c < len(cells) else "")
            parts.append(f" {txt[: col_w[c]].ljust(col_w[c])} ")
        return "|" + "|".join(parts) + "|"

    def _row_blocks(cells: list[str]) -> list[str]:
        wrapped: list[list[str]] = []
        for c in range(ncol):
            txt = (cells[c] if c < len(cells) else "")
            w = col_w[c]
            if len(txt) <= w:
                wrapped.append([txt])
            else:
                wrapped.append(textwrap.wrap(txt, width=w) or [txt[:w]])
        height = max(len(col) for col in wrapped)
        block: list[str] = []
        for i in range(height):
            line_cells = [
                wrapped[c][i] if i < len(wrapped[c]) else "" for c in range(ncol)
            ]
            block.append(_row(line_cells))
        return block

    out = [_border(), *_row_blocks(rows[0]), _border()]
    for row in rows[1:]:
        out.extend(_row_blocks(row))
    out.append(_border())
    return out