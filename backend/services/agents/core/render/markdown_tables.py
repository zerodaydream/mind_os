"""Normalize GitHub-flavored markdown tables from LLM output."""

from __future__ import annotations

import re

_PLACEHOLDER = re.compile(r"\*\[(Subject|Job|Description|Summary)[^\]]*\]\*", re.I)


def _is_table_line(line: str) -> bool:
    """True when a line looks like a markdown table row."""
    return line.strip().count("|") >= 2


def _is_separator_line(line: str) -> bool:
    """True for markdown header separator rows."""
    t = line.strip()
    if not t or not _is_table_line(t):
        return False
    cells = _split_cells(t)
    if not cells:
        return False
    for cell in cells:
        core = cell.strip().replace(":", "").strip()
        if not core or not set(core) <= {"-"}:
            return False
    return True


def _split_cells(line: str) -> list[str]:
    """Split a pipe table row into cell strings."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _placeholder_row(cells: list[str]) -> bool:
    """True when row is LLM placeholder tokens only."""
    joined = " | ".join(cells)
    return bool(_PLACEHOLDER.search(joined)) or "snippet]*" in joined.lower()


def _format_table(rows: list[list[str]]) -> list[str]:
    """Render rows as aligned markdown table lines."""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(ncol)) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def table_has_body_rows(text: str) -> bool:
    """True when a markdown table includes at least one non-header row."""
    past_sep = False
    for ln in text.splitlines():
        if not _is_table_line(ln):
            continue
        if _is_separator_line(ln):
            past_sep = True
            continue
        if past_sep:
            cells = _split_cells(ln)
            if cells and not _placeholder_row(cells):
                return True
    return False


def answer_has_empty_table_shell(text: str) -> bool:
    """True when answer has table headers but no body rows."""
    if not any(_is_table_line(ln) for ln in text.splitlines()):
        return False
    return not table_has_body_rows(text)


def gmail_rows_markdown_table(rows: list[dict], *, limit: int = 10) -> str:
    """Markdown table for gmail_ctx dict rows."""
    from backend.services.agents.core.render.mail_display import format_mail_display_when

    data = [
        ["Subject", "Direction", "Sent", "To", "From"],
    ]
    for g in rows[:limit]:
        data.append(
            [
                (g.get("title") or "(no subject)")[:100],
                (g.get("direction") or "?")[:12],
                format_mail_display_when(g.get("mail_at")),
                ((g.get("to_addr") or "?")[:80]),
                ((g.get("from_addr") or "?")[:80]),
            ]
        )
    if len(data) < 2:
        return ""
    return "\n".join(_format_table(data))


_LIST_NUM_GLUE = re.compile(r"^(\s*)(\d+)([A-Za-z])")
_INLINE_BULLET = re.compile(r"(?<!\n)\s+•\s+")
_EM_DASH_URL = re.compile(r"\s+—\s+(?=https?://)")


def _polish_prose_line(line: str) -> str:
    if not line.strip():
        return line
    if _is_table_line(line):
        return line
    if "://" in line:
        s = line
    else:
        s = line
        s = _LIST_NUM_GLUE.sub(r"\1\2. \3", s)
        s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
        s = re.sub(r"(\d)([A-Za-z])", r"\1 \2", s)
        s = re.sub(r"([a-z])([A-Z][a-z])", r"\1 \2", s)
    s = _INLINE_BULLET.sub("\n• ", s)
    s = _EM_DASH_URL.sub("\n  ", s)
    return s


def polish_agent_answer(text: str) -> str:
    """Fix common LLM spacing: glued words, inline bullets, URLs after em dashes."""
    if not (text or "").strip():
        return text or ""
    out: list[str] = []
    for raw in text.splitlines():
        chunk = _polish_prose_line(raw)
        out.extend(chunk.splitlines())
    collapsed: list[str] = []
    blanks = 0
    for ln in out:
        if not ln.strip():
            blanks += 1
            if blanks <= 2:
                collapsed.append("")
            continue
        blanks = 0
        collapsed.append(ln.rstrip())
    return "\n".join(collapsed).strip()


def normalize_markdown_tables(text: str) -> str:
    """Fix placeholder and broken tables in LLM markdown."""
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
            if out and out[-1].strip():
                out.append("")
            out.extend(_format_table(rows))
            out.append("")
        else:
            out.extend(block)
    return "\n".join(out)