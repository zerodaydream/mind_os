"""ASCII box tables for curses + plain terminal (dynamic width from content + terminal)."""

from __future__ import annotations

import shutil

_G = {
    "tl": "+",
    "tr": "+",
    "bl": "+",
    "br": "+",
    "h": "-",
    "v": "|",
    "tm": "+",
    "bm": "+",
    "ml": "+",
    "mr": "+",
    "x": "+",
    "arr": "→",
}


def term_width(*, fallback: int = 80) -> int:
    try:
        return shutil.get_terminal_size(fallback=(fallback, 24)).columns
    except OSError:
        return fallback


def hline(width: int) -> str:
    inner = max(0, width - 2)
    return f"+{'-' * inner}+"


def boxed_row(text: str, width: int) -> str:
    """One row inside a +---+ box; width includes corner columns."""
    inner = max(0, width - 2)
    body = text[:inner]
    return f"{_G['v']}{body}{' ' * (inner - len(body))}{_G['v']}"


def rule(width: int) -> str:
    return "-" * max(1, width)


def frame_title(title: str, *, width: int | None = None) -> list[str]:
    w = _clamp_width(width or term_width())
    label = f" {title.strip()} "
    if len(label) > w - 2:
        label = f" {title.strip()[: max(4, w - 4)]} "
    fill = max(0, w - len(label))
    left = fill // 2
    return [f"+{'-' * left}{label}{'-' * (fill - left)}+"]


def footer_hint(text: str, *, width: int) -> str:
    text = text.strip()
    inner = max(0, width - 2)
    body = f" {text} "
    if len(body) > inner:
        body = f" {text[: max(4, inner - 4)]} "
    pad = max(0, inner - len(body))
    return f"+{body}{' ' * pad}+"


def _clamp_width(width: int, *, hard_cap: int | None = None) -> int:
    cap = hard_cap if hard_cap is not None else term_width()
    return max(24, min(width, cap))


def _center(text: str, width: int) -> str:
    text = text[:width]
    if len(text) >= width:
        return text
    pad = width - len(text)
    return " " * (pad // 2) + text + " " * (pad - pad // 2)


def _pad_cell(text: str, width: int) -> str:
    text = text[:width]
    return text + " " * (width - len(text))


def _col_widths(
    rows: list[tuple[str, str]],
    headers: tuple[str, str],
    width: int,
) -> tuple[int, int, int]:
    """left_w, right_w, inner body between side bars."""
    total = _clamp_width(width, hard_cap=width)
    pair = max(12, total - 9)
    right_need = max(len(headers[1]), max((len(b) for _, b in rows), default=0))
    right_w = max(10, min(right_need, max(10, pair // 3)))
    left_w = max(6, pair - right_w)
    body = left_w + right_w + 7
    return left_w, right_w, body


def _data_row(left_w: int, right_w: int, left: str, right: str, *, mark: str) -> str:
    g = _G
    return f"{g['v']} {mark}{_pad_cell(left, left_w)} {g['v']} {_pad_cell(right, right_w)} {g['v']}"


def _fit_lines(lines: list[str], line_w: int) -> list[str]:
    out: list[str] = []
    for ln in lines:
        if len(ln) >= line_w:
            out.append(ln[:line_w])
        else:
            out.append(ln + " " * (line_w - len(ln)))
    return out


def _table_core(
    rows: list[tuple[str, str]],
    *,
    width: int,
    headers: tuple[str, str],
    selected: int | None,
) -> tuple[list[str], int]:
    g = _G
    left_w, right_w, _body = _col_widths(rows, headers, width)
    span_l, span_r = left_w + 3, right_w + 3
    top = f"{g['tl']}{g['h'] * span_l}{g['tm']}{g['h'] * span_r}{g['tr']}"
    line_w = len(top)
    head = _data_row(left_w, right_w, headers[0], headers[1], mark=" ")
    mid = f"{g['ml']}{g['h'] * span_l}{g['x']}{g['h'] * span_r}{g['mr']}"
    body_lines = [top, head, mid]
    for i, (left, right) in enumerate(rows):
        mark = g["arr"] if selected is not None and i == selected else " "
        body_lines.append(_data_row(left_w, right_w, left, right, mark=mark))
    body_lines.append(f"{g['bl']}{g['h'] * span_l}{g['bm']}{g['h'] * span_r}{g['br']}")
    return _fit_lines(body_lines, line_w), line_w


def menu_panel(
    title: str,
    rows: list[tuple[str, str]],
    *,
    width: int | None = None,
    headers: tuple[str, str] = ("Command", "Description"),
    selected: int | None = None,
    subtitle: str = "",
) -> list[str]:
    w = _clamp_width(width or term_width() - 4, hard_cap=width)
    left_w, right_w, body = _col_widths(rows, headers, w)
    g = _G
    span_l, span_r = left_w + 3, right_w + 3
    top = f"{g['tl']}{g['h'] * span_l}{g['tm']}{g['h'] * span_r}{g['tr']}"
    line_w = len(top)

    title_row = f"{g['v']}{_center(title.strip(), body)}{g['v']}"
    sub_row = f"{g['v']}{_center(subtitle.strip(), body)}{g['v']}" if subtitle else ""
    title_sep = f"{g['ml']}{g['h'] * body}{g['mr']}"

    table, _ = _table_core(rows, width=w, headers=headers, selected=selected)
    # table[0] duplicates top — reuse table body from header row down
    out = [top, title_row]
    if sub_row:
        out.append(sub_row)
    out.append(title_sep)
    out.extend(table[1:])
    return _fit_lines(out, line_w)


def two_col_table(
    rows: list[tuple[str, str]],
    *,
    width: int | None = None,
    headers: tuple[str, str] = ("Command", "Description"),
    selected: int | None = None,
) -> list[str]:
    w = _clamp_width(width or term_width() - 4, hard_cap=width)
    lines, _ = _table_core(rows, width=w, headers=headers, selected=selected)
    return lines


def session_table(
    rows: list[tuple[str, str]],
    *,
    width: int | None = None,
    selected: int | None = None,
    title: str = "chat history",
) -> list[str]:
    return menu_panel(
        title,
        rows,
        width=width,
        headers=("Session", "Meta"),
        selected=selected,
    )


_BRAND_MIND = (
    "███╗   ███╗██╗███╗   ██╗██████╗ ",
    "████╗ ████║██║████╗  ██║██╔═══██╗",
    "██╔████╔██║██║██╔██╗ ██║██║   ██║",
    "██║╚██╔╝██║██║██║╚██╗██║██║   ██║",
    "██║ ╚═╝ ██║██║██║ ╚████║██████ ╔╝",
    "╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚══════╝",
)
_BRAND_OS = (
    " ██████╗  ███████╗",
    "██╔═══██╗██╔════╝",
    "██║   ██║███████╗",
    "██║   ██║╚════██║",
    "╚██████╔╝███████║",
    " ╚═════╝ ╚══════╝",
)


def brand_mark_lines(*, max_w: int = 80) -> list[str]:
    """Block figlet MIND + OS on one row each, side by side."""
    cap = max(24, max_w)
    gap = "  "
    mind_w = max(len(m.rstrip()) for m in _BRAND_MIND)
    out: list[str] = []
    for mind, os in zip(_BRAND_MIND, _BRAND_OS, strict=True):
        row = mind.rstrip().ljust(mind_w) + gap + os.strip()
        out.append(row[:cap].rstrip())
    return out


def brand_block_x(width: int, lines: list[str]) -> int:
    if not lines:
        return 0
    block_w = max(len(ln) for ln in lines)
    return max(0, (width - block_w) // 2)


def sink_command_rows(rows: list[tuple[str, str]]) -> list[str]:
    return two_col_table(rows, selected=None)
