"""Normalize GitHub-flavored markdown tables from LLM output."""

from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\*\[(Subject|Job|Description|Summary)[^\]]*\]\*", re.I)


def _is_table_line(line: str) -> bool:
    return line.strip().count("|") >= 2


def _is_separator_line(line: str) -> bool:
    t = line.strip()
    return bool(t) and "-" in t and re.match(r"^\|?[\s:|-]+\|?", t) is not None


def _split_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _placeholder_row(cells: list[str]) -> bool:
    joined = " | ".join(cells)
    return bool(_PLACEHOLDER.search(joined)) or "snippet]*" in joined.lower()


def _format_table(rows: list[list[str]]) -> list[str]:
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(ncol)) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def normalize_markdown_tables(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if not _is_table_line(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        block: list[str] = []
        while i < len(lines) and lines[i].strip() and (
            _is_table_line(lines[i]) or _is_separator_line(lines[i])
        ):
            block.append(lines[i])
            i += 1
        rows: list[list[str]] = []
        for ln in block:
            if _is_separator_line(ln):
                continue
            cells = _split_cells(ln)
            if cells and not _placeholder_row(cells):
                rows.append(cells)
        if len(rows) >= 1:
            out.extend(_format_table(rows))
        else:
            out.extend(block)
    return "\n".join(out)