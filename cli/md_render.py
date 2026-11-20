"""Lightweight markdown → terminal layout (tables as ASCII grids)."""

from __future__ import annotations

import re
import textwrap

_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
_HEADING_MARKS = re.compile(r"#{1,6}\s+")
_NOTE = re.compile(r"^\*\((.+)\)\*\s*$")
_LIST = re.compile(r"^(\s*)([-*•]|\d+\.)\s+(.*)$")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
_BARE_URL = re.compile(r"https?://[^\s\]\)]+")
_LIST_LINE_URL = re.compile(r"^(\s*)(\d+\.)\s+(https?://\S+)\s*$")


def is_http_url(text: str) -> bool:
    s = text.strip()
    return s.startswith("http://") or s.startswith("https://")


def strip_inline_md(text: str) -> str:
    text = _BOLD.sub(r"\1", text)
    text = _ITALIC.sub(r"\1", text)
    return text.strip()


def strip_heading_marks(text: str) -> str:
    return _HEADING_MARKS.sub("", text)


def short_link_label(url: str) -> str:
    from urllib.parse import urlparse

    u = url.strip().rstrip(".,;")
    p = urlparse(u)
    host = (p.netloc or "").removeprefix("www.")
    if "drive.google.com" in host:
        return "Google Drive"
    if "meet.google.com" in host or "zoom.us" in host:
        return "Meeting link"
    if "calendar.google.com" in host:
        return "Calendar"
    name = (p.path or "").strip("/").split("/")[-1]
    if name and len(name) <= 48:
        return name.replace("%20", " ")
    return host or u[:56]


def _list_link_block(indent: str, marker: str, label: str, url: str) -> str:
    """Title line, indented full URL on the next (copy-friendly)."""
    url = url.strip().rstrip(".,;")
    title = strip_inline_md(label).strip() if label else short_link_label(url)
    if not title or title == url:
        title = short_link_label(url)
    child = f"{indent}  {url}"
    return f"{indent}{marker} {title}\n{child}"


def prepare_chat_line(line: str) -> str:
    """Markdown/bare URLs → readable label + indented full URL (no orphan “Link” lines)."""
    raw = line.rstrip()
    stripped = raw.strip()
    if is_http_url(stripped):
        return stripped

    lm = _LIST.match(raw)
    if lm:
        body = lm.group(3).strip()
        link_m = _MD_LINK.fullmatch(body)
        if link_m:
            return _list_link_block(
                lm.group(1), lm.group(2), link_m.group(1), link_m.group(2)
            )
        if is_http_url(body):
            return _list_link_block(lm.group(1), lm.group(2), "", body)

    num = _LIST_LINE_URL.match(raw)
    if num:
        return _list_link_block(num.group(1), num.group(2), "", num.group(3))

    def _inline_link(match: re.Match[str]) -> str:
        label, url = match.group(1).strip(), match.group(2).strip()
        title = strip_inline_md(label) if label else short_link_label(url)
        if not title or title == url:
            title = short_link_label(url)
        return f"{title}\n  {url.strip()}"

    out = _MD_LINK.sub(_inline_link, raw)
    return _BARE_URL.sub(lambda m: m.group(0).rstrip(".,;"), out)


def _line_urls(line: str) -> list[str]:
    urls = [m.group(2) for m in _MD_LINK.finditer(line)]
    if urls:
        return urls
    return _BARE_URL.findall(line)


def wants_legacy_chat_md(text: str) -> bool:
    """Pipe tables and our two-line Drive links need the hand-rolled renderer."""
    for raw in text.splitlines():
        if is_table_line(raw):
            return True
        if re.match(r"^\s{2,}https?://", raw):
            return True
    return False


MdChatRow = tuple[str, str] | tuple[str, str, list[tuple[str, str]]]


def _rich_style_key(style: object | None) -> str:
    s = str(style or "").lower()
    if not s or s == "none":
        return "body"
    if "markdown.code" in s or s.startswith("repr") or "green" in s:
        return "code"
    if "magenta" in s or any(h in s for h in ("markdown.h1", "markdown.h2", "markdown.h3")):
        return "heading"
    if "bold" in s:
        return "strong"
    if "dim" in s or "markdown.block_quote" in s:
        return "meta"
    if "markdown.link" in s:
        return "link"
    return "body"


def _merge_style_spans(spans: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key, text in spans:
        if not text:
            continue
        if out and out[-1][0] == key:
            out[-1] = (key, out[-1][1] + text)
        else:
            out.append((key, text))
    return out


def _row_style(line: str, spans: list[tuple[str, str]]) -> str:
    if line.lstrip().startswith("•"):
        return "list"
    if is_http_url(line.strip()) or line.strip().startswith("↳"):
        return "link"
    if re.match(r"(?i)^source(?:\s*excerpt|\(s\))?(?:\*\*)?:", line.strip()):
        return "meta"
    keys = {k for k, _ in spans}
    if "heading" in keys:
        return "heading"
    if "code" in keys and len(keys) == 1:
        return "code"
    return spans[0][0] if spans else "body"


def rich_chat_span_lines(text: str, width: int) -> list[MdChatRow] | None:
    if wants_legacy_chat_md(text):
        return None
    try:
        from rich.console import Console
        from rich.markdown import Markdown
    except ImportError:
        return None
    width = max(20, width)
    console = Console(width=width, force_terminal=True, color_system="standard")
    opts = console.options.update_width(width)
    out: list[MdChatRow] = []
    try:
        for line_segs in console.render_lines(Markdown(text), opts):
            raw_spans = [
                (_rich_style_key(seg.style), seg.text)
                for seg in line_segs
                if seg.text
            ]
            spans = _merge_style_spans(raw_spans)
            line = "".join(part for _, part in spans).rstrip()
            if not line.strip():
                out.append(("body", ""))
                continue
            style = _row_style(line, spans)
            if len(spans) == 1 or len({k for k, _ in spans}) == 1:
                out.append((style, line))
            else:
                trimmed = [(k, t.rstrip()) for k, t in spans if t.rstrip()]
                line = "".join(t for _, t in trimmed).rstrip()
                out.append((style, line, trimmed))
    except Exception:
        return None
    return out if out else None


def prepare_chat_text(text: str) -> str:
    from backend.services.agents.core.render.markdown_tables import polish_agent_answer

    out: list[str] = []
    prev_key = ""
    blocks = text.split("\n\n")
    for bi, block in enumerate(blocks):
        if bi > 0:
            out.append("")
        polished = polish_agent_answer(block)
        for raw in polished.splitlines():
            line = prepare_chat_line(raw)
            urls = _line_urls(raw)
            key = urls[0] if len(urls) == 1 else ""
            if key and key == prev_key:
                continue
            prev_key = key if len(urls) == 1 else ""
            out.append(line)
    return "\n".join(out)


def display_for_tui(line: str, style: str) -> str:
    """Strip markdown syntax; curses shows style via attributes, not glyphs."""
    raw = line.rstrip()
    if not raw.strip():
        return ""
    if style == "list":
        m = _LIST.match(raw)
        if m:
            marker = "•" if m.group(2) in "-*•" else m.group(2)
            return f"{m.group(1)}{marker} {strip_inline_md(m.group(3))}"
    if style == "link":
        link = strip_inline_md(raw.strip())
        if not link.startswith("↳"):
            link = f"↳ {link}"
        return link
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
        if _BOLD.fullmatch(s):
            return _BOLD.sub(r"\1", s)
    if style == "meta":
        s = s.strip("_")
        m = _NOTE.match(s)
        if m:
            return strip_heading_marks(m.group(1).strip())
        plain = strip_heading_marks(strip_inline_md(s))
        return re.sub(
            r"(?i)^source\s*excerpt:",
            "Source(s):",
            plain,
        )
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
    url_cols: set[int] = set()
    for r in rows:
        for c, cell in enumerate(r):
            if c < ncol and is_http_url(cell):
                url_cols.add(c)
                col_w[c] = max(col_w[c], len(cell.strip()))

    def _total() -> int:
        return sum(col_w) + 3 * ncol + 1

    # shortcut: min 6 chars per column; wrap tall cells instead of truncating to 1 char
    _MIN_COL = 6
    while _total() > width and max(col_w) > _MIN_COL:
        shrinkable = [
            c for c in range(ncol) if c not in url_cols and col_w[c] > _MIN_COL
        ]
        if not shrinkable:
            break
        c = max(shrinkable, key=lambda i: col_w[i])
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
            if is_http_url(txt):
                wrapped.append([txt.strip()])
            elif len(txt) <= w:
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