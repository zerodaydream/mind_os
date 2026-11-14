"""Terminal UI — Rich when available, ANSI fallback."""

from __future__ import annotations

import re
import shutil
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from queue import Empty, SimpleQueue

from cli import tui_theme, voice_stt

_MD_TAG = "\x00md:"
_MD_NL = "\x1e"

_sink: list[str] | None = None
_tui_stdscr = None
_persistent_tui = False

_MENU_VALUES = [
    ("chat", "Chat with your indexed context"),
    ("sync", "Sync filtered Gmail"),
    ("sync:process", "Sync Gmail + process outbox"),
    ("status", "Show DB · Gmail · artifact counts"),
    ("exit", "Exit mindos"),
]


def action_begin() -> None:
    global _sink
    _sink = []


def action_end(*, show: bool | None = None) -> list[str]:
    global _sink
    lines = list(_sink or [])
    _sink = None
    if show is None:
        show = not _persistent_tui
    if show and lines and sys.stdout.isatty():
        show_log(lines, footer="Enter → menu")
    elif _persistent_tui and _tui_stdscr is not None and lines:
        _show_sink(_tui_stdscr, lines)
    return lines


def tui_flush() -> None:
    lines = action_end(show=False)
    if lines and _tui_stdscr is not None:
        _show_sink(_tui_stdscr, lines)


def tui_busy(fn, *, label: str = "Working"):
    """Spinner while a blocking CLI action runs."""
    stdscr = _tui_stdscr
    if stdscr is None:
        if _RICH and _CONSOLE:
            with _CONSOLE.status(f"[bold yellow]{label}[/]", spinner="dots"):
                return fn()
        return fn()

    stop = threading.Event()
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def _spin() -> None:
        i = 0
        while not stop.is_set():
            _normal, sel = _curses_harvest(stdscr)
            h, w = stdscr.getmaxyx()
            stdscr.erase()
            msg = f"  {label}  {frames[i % len(frames)]}"
            try:
                col = max(0, (w - len(msg)) // 2)
                stdscr.addstr(max(0, h // 2), col, msg[: w - 1], sel)
            except Exception:
                pass
            try:
                stdscr.refresh()
            except Exception:
                pass
            i += 1
            stop.wait(0.09)

    worker = threading.Thread(target=_spin, daemon=True)
    worker.start()
    try:
        return fn()
    finally:
        stop.set()
        worker.join(timeout=0.5)


def in_menu_session() -> bool:
    return _sink is not None


def in_tui() -> bool:
    return _persistent_tui and _tui_stdscr is not None


def _record(line: str) -> None:
    if _sink is not None:
        _sink.append(line)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.theme import Theme

    _RICH = True
    _CONSOLE = Console(
        theme=Theme(
            {
                "banner": "bold cyan",
                "ok": "bold green",
                "err": "bold red",
                "dim": "dim",
                "accent": "bold yellow",
            }
        )
    )
except ImportError:
    _RICH = False
    _CONSOLE = None


class _C:
    reset = "\033[0m"
    dim = "\033[2m"
    bold = "\033[1m"
    cyan = "\033[36m"
    green = "\033[32m"
    red = "\033[31m"
    yellow = "\033[33m"


def _curses_bright_white(curses_mod, pair_id: int) -> int:
    try:
        curses_mod.init_pair(pair_id, 231, -1)  # xterm bright white
    except curses_mod.error:
        curses_mod.init_pair(pair_id, curses_mod.COLOR_WHITE, -1)
    return curses_mod.color_pair(pair_id) | curses_mod.A_BOLD


def _curses_harvest(stdscr) -> tuple:
    import curses

    curses.curs_set(0)
    curses.use_default_colors()
    normal_attr = curses.A_BOLD
    sel_attr = curses.A_BOLD
    if curses.has_colors():
        curses.start_color()
        try:
            curses.init_pair(1, 226, -1)  # bright yellow highlight
        except curses.error:
            curses.init_pair(1, curses.COLOR_YELLOW, -1)
        normal_attr = _curses_bright_white(curses, 2)
        sel_attr = curses.color_pair(1) | curses.A_BOLD
    return normal_attr, sel_attr


def _curses_brand_attr() -> int:
    import curses

    if curses.has_colors():
        curses.start_color()
        try:
            # system yellow
            curses.init_pair(9, curses.COLOR_YELLOW, -1)
            return curses.color_pair(9) | curses.A_BOLD
        except curses.error:
            pass
    return curses.A_BOLD


def _curses_success_attr() -> int:
    import curses

    if curses.has_colors():
        curses.start_color()
        try:
            curses.init_pair(10, curses.COLOR_GREEN, -1)
            return curses.color_pair(10) | curses.A_BOLD
        except curses.error:
            pass
    return curses.A_BOLD


def _split_sink(lines: list[str]) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    plain: list[str] = []
    for line in lines:
        if line.startswith(_MD_TAG):
            if plain:
                chunks.append(("plain", "\n".join(plain)))
                plain = []
            body = line[len(_MD_TAG) :].replace(_MD_NL, "\n")
            chunks.append(("md", body))
        else:
            plain.append(line)
    if plain:
        chunks.append(("plain", "\n".join(plain)))
    return chunks


def _rich_markdown_view(stdscr, body: str) -> None:
    import curses

    curses.curs_set(0)
    curses.def_prog_mode()
    curses.endwin()
    try:
        w = max(40, shutil.get_terminal_size(fallback=(80, 24)).columns)
        if _RICH and _CONSOLE:
            from rich.markdown import Markdown
            from rich.panel import Panel

            console = Console(width=w, force_terminal=True)
            console.print(Panel(Markdown(body), title="mindos", border_style="white"))
            console.print("[dim]Enter → continue[/]")
        else:
            sys.stdout.write(body + "\n\nEnter → continue\n")
        sys.stdout.flush()
        try:
            import termios
            import tty

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setcbreak(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\n", "\r", "q", "\x1b"):
                        break
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except Exception:
            sys.stdin.readline()
    finally:
        curses.reset_prog_mode()
        try:
            stdscr.clear()
            stdscr.refresh()
        except Exception:
            pass


def _show_sink(stdscr, lines: list[str]) -> None:
    for kind, content in _split_sink(lines):
        if kind == "md" and content.strip():
            _rich_markdown_view(stdscr, content)
        elif kind == "plain" and content.strip():
            _log_draw(stdscr, content.splitlines())


def _log_draw(stdscr, lines: list[str], *, footer: str = "↑/↓ scroll · Enter → menu") -> None:
    import curses as c

    if not lines:
        return
    normal_attr, sel_attr = _curses_harvest(stdscr)
    offset = 0
    while True:
        h, w = stdscr.getmaxyx()
        if w < 10 or h < 4:
            return
        flat: list[str] = []
        for raw in lines:
            flat.extend(textwrap.wrap(raw, width=max(w - 1, 20)) or [""])
        max_off = max(0, len(flat) - (h - 2))
        offset = min(offset, max_off)
        stdscr.erase()
        try:
            stdscr.addstr(0, 0, tui_theme.hline(max(10, w - 1))[: w - 1], normal_attr)
        except c.error:
            pass
        view_h = h - 2
        for i in range(view_h):
            li = offset + i
            if li >= len(flat):
                break
            try:
                stdscr.addstr(i + 1, 0, flat[li][: w - 1], normal_attr)
            except c.error:
                pass
        try:
            stdscr.addstr(h - 1, 0, footer[: w - 1], sel_attr)
        except c.error:
            pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (c.KEY_ENTER, 10, 13, ord("\n"), ord("q"), 27):
            return
        if key in (c.KEY_UP, ord("k")):
            offset = max(0, offset - 1)
        elif key in (c.KEY_DOWN, ord("j")):
            offset = min(max_off, offset + 1)
        elif key in (c.KEY_PPAGE,):
            offset = max(0, offset - view_h)
        elif key in (c.KEY_NPAGE,):
            offset = min(max_off, offset + view_h)


def show_log(lines: list[str], *, footer: str = "Enter → menu") -> None:
    import curses

    if not lines or not sys.stdout.isatty():
        for line in lines:
            sys.stdout.write(line + "\n")
        return
    if _tui_stdscr is not None:
        _show_sink(_tui_stdscr, lines)
        return

    def _run(stdscr: curses.window) -> None:
        _log_draw(stdscr, lines, footer=footer)

    try:
        curses.wrapper(_run)
    except Exception:
        for line in lines:
            sys.stdout.write(line + "\n")


def _menu_input_draw(stdscr, prompt: str) -> str:
    import curses as c

    normal_attr, sel_attr = _curses_harvest(stdscr)
    c.curs_set(1)
    buf = ""
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        try:
            stdscr.addstr(0, 0, "mindos · chat", normal_attr | c.A_BOLD)
            stdscr.addstr(1, 0, "type your question · Enter send · Esc menu", normal_attr)
            stdscr.addstr(h - 2, 0, prompt[: w - 1], normal_attr)
            stdscr.addstr(h - 1, 0, buf[: w - 1], sel_attr)
        except c.error:
            pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (c.KEY_ENTER, 10, 13, ord("\n")):
            c.curs_set(0)
            return buf.strip()
        if key in (27,):
            c.curs_set(0)
            return ""
        if key in (c.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= key <= 126 and len(buf) < 4000:
            buf += chr(key)


def menu_input(prompt: str) -> str:
    import curses

    if _tui_stdscr is not None:
        return _menu_input_draw(_tui_stdscr, prompt)
    if not sys.stdin.isatty():
        return prompt_line(prompt)

    def _run(stdscr: curses.window) -> str:
        return _menu_input_draw(stdscr, prompt)

    try:
        return curses.wrapper(_run)
    except Exception:
        return prompt_line(prompt)


def prompt_line(prompt: str) -> str:
    if _RICH and _CONSOLE and _sink is None:
        return _CONSOLE.input(f"[bold cyan]{prompt}[/] ").strip()
    sys.stdout.write(prompt)
    sys.stdout.flush()
    return sys.stdin.readline().strip()


def banner() -> None:
    title = "mindos"
    subtitle = "local context · gmail · notes"
    if _sink is not None:
        _record(f"{title}  {subtitle}")
        return
    if _RICH and _CONSOLE:
        _CONSOLE.print(
            Panel(
                Text.from_markup(f"[banner]{title}[/]  [dim]{subtitle}[/]"),
                border_style="cyan",
                padding=(0, 1),
            )
        )
        return
    w = min(shutil.get_terminal_size(fallback=(72, 24)).columns, 72)
    frame = tui_theme.frame_title(f"{title}  ·  {subtitle}", width=w)
    sys.stdout.write("\n")
    for line in frame:
        sys.stdout.write(f"{_C.cyan}{_C.bold}{line}{_C.reset}\n")


def step(label: str, detail: str = "", *, ok: bool | None = None) -> None:
    if _sink is not None:
        mark = "✓" if ok is True else "✗" if ok is False else "·"
        extra = f"  {detail}" if detail else ""
        lbl = f"{label}  " if label else ""
        _record(f"  {mark}  {lbl}{extra}".rstrip())
        return
    if _RICH and _CONSOLE:
        if ok is True:
            _CONSOLE.print(f"  [ok]✓[/]  {label}  [dim]{detail}[/]")
        elif ok is False:
            _CONSOLE.print(f"  [err]✗[/]  {label}  [dim]{detail}[/]")
        else:
            _CONSOLE.print(f"  [dim]·[/]  {label}  [dim]{detail}[/]")
        return
    mark, color = "·", _C.dim
    if ok is True:
        mark, color = "✓", _C.green
    elif ok is False:
        mark, color = "✗", _C.red
    extra = f"  {_C.dim}{detail}{_C.reset}" if detail else ""
    sys.stdout.write(f"  {color}{mark}{_C.reset}  {label}{extra}\n")


def note(text: str) -> None:
    if _sink is not None:
        for line in text.splitlines() or [text]:
            _record(line)
        return
    if _RICH and _CONSOLE:
        _CONSOLE.print(Panel(text, border_style="dim", padding=(0, 1)))
        return
    sys.stdout.write(f"\n  {_C.dim}{text}{_C.reset}\n\n")


def note_md(text: str) -> None:
    """Record markdown for Rich rendering in the TUI."""
    if _sink is not None:
        encoded = text.replace("\n", _MD_NL)
        _record(f"{_MD_TAG}{encoded}")
        return
    if _RICH and _CONSOLE:
        from rich.markdown import Markdown

        _CONSOLE.print(Markdown(text))
        return
    note(text)


def error(text: str) -> None:
    if _sink is not None:
        _record(f"error: {text}")
        return
    if _RICH and _CONSOLE:
        _CONSOLE.print(f"[err]error:[/] {text}")
        return
    sys.stderr.write(f"{_C.red}error:{_C.reset} {text}\n")


def _menu_draw(
    stdscr,
    values: list[tuple[str, str]],
    *,
    title: str,
    header_lines: list[str],
) -> str | None:
    import curses

    stdscr.keypad(True)
    normal_attr, sel_attr = _curses_harvest(stdscr)
    idx = 0
    panel_scroll = 0
    esc = 27
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        table_w = min(max(52, w - 4), 88)
        subtitle = ""
        for line in header_lines:
            low = line.lower()
            if "project:" in low or "mindos_home" in low:
                subtitle = line.strip()
                break
        panel = tui_theme.menu_panel(
            title,
            list(values),
            width=table_w,
            selected=idx,
            subtitle=subtitle,
        )
        help_text = "↑↓ move · Enter run · Esc back"
        foot_attr = normal_attr
        try:
            foot_attr = normal_attr | curses.A_DIM
        except curses.error:
            pass
        brand = tui_theme.brand_mark_lines(max_w=w)
        brand_h = len(brand) + (1 if brand else 0)
        if brand_h + len(panel) + 1 > h:
            brand = []
            brand_h = 0
        brand_attr = _curses_brand_attr()
        block_h = brand_h + len(panel)
        y0 = max(0, (h - 1 - block_h) // 2) if block_h + 1 <= h else 0
        if brand:
            bx = tui_theme.brand_block_x(w, brand)
            for bi, ln in enumerate(brand):
                try:
                    stdscr.addstr(y0 + bi, bx, ln[: max(1, w - bx)], brand_attr)
                except curses.error:
                    pass
            y0 += brand_h
        x0 = max(0, (w - len(panel[0])) // 2)
        panel_top = y0
        avail = max(1, h - 1 - panel_top)

        def _data_index(line_i: int) -> int | None:
            # panel: top, title, [sub], sep, head, mid, data..., bottom
            base = 3 + (1 if subtitle else 0)
            if line_i < base + 2 or line_i >= len(panel) - 1:
                return None
            return line_i - (base + 2)

        base = 3 + (1 if subtitle else 0)
        sel_row = base + 2 + idx
        if len(panel) > avail:
            if sel_row < panel_scroll:
                panel_scroll = sel_row
            elif sel_row >= panel_scroll + avail:
                panel_scroll = sel_row - avail + 1
            panel_scroll = max(0, min(panel_scroll, len(panel) - avail))
        else:
            panel_scroll = 0

        for i, line in enumerate(panel):
            sy = panel_top + i - panel_scroll
            if sy < panel_top or sy >= h - 1:
                continue
            attr = normal_attr
            if i == 1 or (subtitle and i == 2):
                attr = normal_attr | curses.A_BOLD
            di = _data_index(i)
            if di is not None and di == idx:
                attr = sel_attr
            try:
                stdscr.addstr(sy, x0, line, attr)
            except curses.error:
                pass
        if h > 0:
            hx = max(0, (w - len(help_text)) // 2)
            try:
                stdscr.addstr(h - 1, hx, help_text[: max(1, w - hx)], foot_attr)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            idx = (idx - 1) % len(values)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = (idx + 1) % len(values)
        elif key in (curses.KEY_ENTER, 10, 13, ord("\n")):
            return values[idx][0]
        elif key in (esc,):
            try:
                curses.flushinp()
            except curses.error:
                pass
            return None


def _tui_flash(stdscr, message: str, *, wait_s: float = 2.0, attr: int | None = None) -> None:
    import curses
    import time

    normal_attr, _ = _curses_harvest(stdscr)
    draw_attr = normal_attr if attr is None else attr
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    text = message.strip()[: max(1, w - 2)]
    try:
        stdscr.addstr(max(0, h // 2), max(0, (w - len(text)) // 2), text, draw_attr)
    except curses.error:
        pass
    stdscr.refresh()
    time.sleep(wait_s)


def _ascii_labeled_box(label: str) -> list[str]:
    body = f" {label.strip()} "
    bar = "-" * len(body)
    return [f"+{bar}+", f"|{body}|", f"+{bar}+"]


def _lr_yes_no_dialog(stdscr, *, prompt: str) -> bool | None:
    """Yes (left) / Cancel (right). Enter confirms; Esc cancels."""
    import curses

    normal_attr, sel_attr = _curses_harvest(stdscr)
    nav_attr = normal_attr
    try:
        nav_attr = normal_attr | curses.A_DIM
    except curses.error:
        pass
    nav = "*use ← → to navigate*"
    labels = ("Yes", "Cancel")
    boxes = [_ascii_labeled_box(lab) for lab in labels]
    choice = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        wrap_w = max(12, w - 6)
        prompt_lines = textwrap.wrap(prompt.strip(), width=wrap_w) or [""]
        box_h = 3
        gap = max(2, w // 16)
        box_ws = [len(b[0]) for b in boxes]
        row_w = box_ws[0] + gap + box_ws[1]
        block_h = len(prompt_lines) + 1 + box_h
        start_y = max(0, (h - block_h - 1) // 2)
        for i, line in enumerate(prompt_lines):
            x = max(0, (w - len(line)) // 2)
            try:
                stdscr.addstr(start_y + i, x, line[: max(1, w - x)], normal_attr)
            except curses.error:
                pass
        btn_y = start_y + len(prompt_lines) + 1
        btn_x = max(0, (w - row_w) // 2)
        for bi, box in enumerate(boxes):
            x = btn_x if bi == 0 else btn_x + box_ws[0] + gap
            selected = bi == choice
            for j, row in enumerate(box):
                attr = normal_attr
                if selected:
                    attr = sel_attr | (curses.A_BOLD if j == 1 else 0)
                try:
                    stdscr.addstr(btn_y + j, x, row[: max(1, w - x)], attr)
                except curses.error:
                    pass
        if h > 0:
            try:
                stdscr.addstr(h - 1, max(0, (w - len(nav)) // 2), nav[: max(1, w - 1)], nav_attr)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        enter_key = False
        if key == 27:
            kind = _read_esc_csi_action(stdscr, restore_ms=-1)
            if kind == "enter":
                enter_key = True
            elif kind == "left":
                choice = 0
                continue
            elif kind == "right":
                choice = 1
                continue
            elif kind == "bare_esc":
                return None
            continue
        if key in (curses.KEY_LEFT, ord("h")):
            choice = 0
        elif key in (curses.KEY_RIGHT, ord("l")):
            choice = 1
        elif key in (curses.KEY_ENTER, 10, 13, 343, ord("\n")) or enter_key:
            return choice == 0
        elif key in (27, ord("b")):
            return None


def _session_menu_draw(
    stdscr,
    sessions: list[tuple[str, str, str]],
    *,
    delete_session: Callable[[str], bool] | None = None,
) -> str | None:
    """Pick a chat session. sessions: (id, title, meta). Esc cancels (stays in chat)."""
    import curses

    if not sessions:
        return None
    stdscr.keypad(True)
    normal_attr, sel_attr = _curses_harvest(stdscr)
    idx = 0
    scroll = 0
    data_off = 5

    def _try_delete() -> None:
        nonlocal idx
        sid, title, _meta = sessions[idx]
        if delete_session is None or sid == "__new__":
            return
        name = (title or "Untitled")[:48]
        if _lr_yes_no_dialog(stdscr, prompt=f"Are you sure you want to delete [{name}]?") is not True:
            return
        if not delete_session(sid):
            return
        sessions.pop(idx)
        _tui_flash(stdscr, "Deleted successfully", wait_s=2.0, attr=_curses_success_attr())
        if not sessions:
            return
        idx = min(idx, len(sessions) - 1)

    while True:
        if not sessions:
            return None
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        x0 = 0
        table_w = max(24, w)
        saved_n = sum(1 for sid, _, _ in sessions if sid != "__new__")
        n_w = max(1, len(str(saved_n))) if saved_n else 1
        sess_rows: list[tuple[str, str]] = []
        seq = 0
        for sid, title, meta in sessions:
            if sid == "__new__":
                left = (title or "New chat")[:48]
            else:
                seq += 1
                left = f"{seq:>{n_w}}. {(title or 'Untitled')[: max(8, 48 - n_w - 2)]}"
            sess_rows.append((left, meta or ""))
        brand = tui_theme.brand_mark_lines(max_w=w) if h >= 24 else []
        brand_h = len(brand) + (1 if brand else 0)
        brand_attr = _curses_brand_attr()
        visible = max(1, h - brand_h - data_off - 2)
        if idx < scroll:
            scroll = idx
        elif idx >= scroll + visible:
            scroll = max(0, idx - visible + 1)
        slice_rows = sess_rows[scroll : scroll + visible]
        local_sel = idx - scroll if 0 <= idx - scroll < len(slice_rows) else None
        panel = tui_theme.session_table(
            slice_rows,
            width=table_w,
            selected=local_sel,
            title="Mind OS · saved chats",
        )
        panel_y0 = 0
        if brand:
            bx = tui_theme.brand_block_x(w, brand)
            for bi, ln in enumerate(brand):
                try:
                    stdscr.addstr(bi, bx, ln[: max(1, w - bx)], brand_attr)
                except curses.error:
                    pass
            panel_y0 = brand_h
        del_hint = " · d delete" if delete_session else ""
        help_text = (
            f"Esc back · Enter open · ↑/↓ move{del_hint} · {idx + 1}/{len(sessions)}"
        )
        foot_attr = normal_attr
        try:
            foot_attr = normal_attr | curses.A_DIM
        except curses.error:
            pass
        for i, line in enumerate(panel):
            y = panel_y0 + i
            if y >= h - 1:
                break
            attr = normal_attr
            if i == 1:
                attr = normal_attr | curses.A_BOLD
            elif (
                data_off <= i < len(panel) - 1
                and local_sel is not None
                and i == data_off + local_sel
            ):
                attr = sel_attr
            draw = line[: w - x0] if x0 else line[:w]
            try:
                stdscr.addstr(y, x0, draw, attr)
            except curses.error:
                pass
        if h > 0:
            hx = max(0, (w - len(help_text)) // 2)
            try:
                stdscr.addstr(h - 1, hx, help_text[: max(1, w - hx)], foot_attr)
            except curses.error:
                pass
        stdscr.refresh()
        key = stdscr.getch()
        enter_key = False
        if key == 27:
            kind = _read_esc_csi_action(stdscr, restore_ms=-1)
            if kind == "enter":
                enter_key = True
            elif kind == "up":
                idx = max(0, idx - 1)
                continue
            elif kind == "down":
                idx = min(len(sessions) - 1, idx + 1)
                continue
            elif kind == "bare_esc":
                return None
            else:
                continue
        if key in (curses.KEY_UP, ord("k")):
            idx = max(0, idx - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            idx = min(len(sessions) - 1, idx + 1)
        elif key in (curses.KEY_PPAGE,):
            idx = max(0, idx - visible)
        elif key in (curses.KEY_NPAGE,):
            idx = min(len(sessions) - 1, idx + visible)
        elif key in (ord("d"), ord("D")):
            _try_delete()
            if not sessions:
                return None
        elif key in (curses.KEY_ENTER, 10, 13, 343, ord("\n")) or enter_key:
            return sessions[idx][0]
        elif key in (27, ord("b")):
            return None


def _pick_command_numbers(values: list[tuple[str, str]]) -> str | None:
    for i, (cmd, desc) in enumerate(values, start=1):
        sys.stdout.write(f"  {i}. {cmd:14} {desc}\n")
    sys.stdout.write("choice #: ")
    sys.stdout.flush()
    raw = sys.stdin.readline().strip()
    if not raw.isdigit():
        return None
    idx = int(raw) - 1
    if 0 <= idx < len(values):
        return values[idx][0]
    return None


def pick_command(*, subtitle: str = "") -> str | None:
    """Arrow keys + Enter menu; returns command id or None on cancel."""
    header = ["↑ / ↓ move  ·  Enter run  ·  Esc back"]
    if subtitle:
        header.insert(0, subtitle)
    if sys.stdin.isatty() and sys.stdout.isatty():
        try:
            import curses

            def _once(stdscr: curses.window) -> str | None:
                return _menu_draw(
                    stdscr,
                    _MENU_VALUES,
                    title="mindos · local context",
                    header_lines=header,
                )

            return curses.wrapper(_once)
        except Exception:
            pass
    return _pick_command_numbers(_MENU_VALUES)


def run_interactive(
    subtitle: str,
    handler,
) -> int:
    """Single TUI session: menu → output → menu (no terminal flash)."""
    global _persistent_tui, _tui_stdscr

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return _repl_plain(subtitle, handler)

    import curses

    base_header = ["↑ / ↓ move  ·  Enter run  ·  Esc cancel selection"]

    def _app(stdscr: curses.window) -> int:
        global _tui_stdscr
        _tui_stdscr = stdscr
        show_subtitle = bool(subtitle)
        while True:
            hdr = [subtitle] if show_subtitle and subtitle else []
            choice = _menu_draw(
                stdscr,
                _MENU_VALUES,
                title="mindos · local context",
                header_lines=hdr,
            )
            show_subtitle = False
            if choice is None:
                continue
            action_begin()
            try:
                keep = handler(choice)
            except (EOFError, KeyboardInterrupt):
                note("bye")
                return 0
            finally:
                action_end()
            if not keep:
                return 0

    _persistent_tui = True
    try:
        return curses.wrapper(_app)
    except Exception:
        _persistent_tui = False
        _tui_stdscr = None
        return _repl_plain(subtitle, handler)
    finally:
        _persistent_tui = False
        _tui_stdscr = None


def _repl_plain(subtitle: str, handler) -> int:
    menu_subtitle = subtitle
    while True:
        try:
            choice = pick_command(subtitle=menu_subtitle)
            menu_subtitle = ""
        except (EOFError, KeyboardInterrupt):
            note("bye")
            return 0
        if choice is None:
            continue
        action_begin()
        try:
            keep = handler(choice)
        finally:
            action_end()
        if not keep:
            return 0
    return 0


def _markdown_line_style(line: str, *, in_sources: bool) -> str:
    s = line.strip()
    if not s:
        return "body"
    if s == "sources:":
        return "sources"
    if in_sources and re.match(r"^\s*\d+\.", line):
        return "source_item"
    if s.startswith("…") or s.startswith("..."):
        return "source_item"
    if re.match(r"^_{1,2}.+_{1,2}$", s):
        return "meta"
    if re.match(r"^#{1,6}\s", s):
        return "heading"
    if s.count("|") >= 2:
        return "table"
    if re.match(r"^[-:|+\s]{3,}$", s.replace("|", "").strip("-: |+")) or re.fullmatch(
        r"[-\s]{3,}", s
    ):
        return "rule"
    if re.match(r"^[-*]\s", s) or re.match(r"^\d+\.\s", s):
        return "list"
    if re.match(r"^[A-Z][^:\n]{2,40}:$", s):
        return "heading"
    return "body"


def _split_plan_prefix(text: str) -> tuple[str, str]:
    """Leading todo/plan block (lists/headings) vs final user-facing answer."""
    lines = text.splitlines()
    if not lines:
        return "", text
    plan: list[str] = []
    i = 0
    saw_body = False
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            if plan and not saw_body:
                i += 1
                break
            if saw_body:
                break
            i += 1
            continue
        style = _markdown_line_style(raw, in_sources=False)
        if style in ("list", "heading", "meta") and not saw_body:
            plan.append(raw)
            i += 1
            continue
        saw_body = True
        break
    if not plan:
        return "", text
    rest = "\n".join(lines[i:]).lstrip("\n")
    return "\n".join(plan).strip(), rest


def _thinking_chat_lines(text: str, width: int) -> list[tuple[str, str]]:
    from cli import md_render

    width = max(20, width)
    out: list[tuple[str, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            out.append(("body", ""))
            i += 1
            continue
        if md_render.is_table_line(raw):
            block: list[str] = []
            while i < len(lines) and lines[i].strip() and (
                md_render.is_table_line(lines[i]) or md_render.is_separator_line(lines[i])
            ):
                block.append(lines[i])
                i += 1
            rows = md_render.parse_pipe_table(block)
            if rows:
                for ln in md_render.ascii_table(rows, width):
                    out.append(("table", ln))
                out.append(("body", ""))
            else:
                for ln in block:
                    out.append(("body", md_render.strip_inline_md(ln)))
            continue
        plain = md_render.strip_inline_md(raw.strip())
        style = _markdown_line_style(raw, in_sources=False)
        if style == "list":
            m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", raw.rstrip())
            if m:
                plain = f"{m.group(1)}{m.group(2)} {md_render.strip_inline_md(m.group(3))}"
        for ln in textwrap.wrap(plain, width=width, break_long_words=False) or [plain]:
            out.append(("body", ln.rstrip()))
        i += 1
    return out or [("body", "(empty)")]


def _markdown_chat_lines(text: str, width: int) -> list[tuple[str, str]]:
    from cli import md_render

    width = max(20, width)
    out: list[tuple[str, str]] = []
    in_sources = False
    lines = md_render.prepare_chat_text(text).splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            out.append(("body", ""))
            i += 1
            continue
        if md_render.is_table_line(raw):
            block: list[str] = []
            while i < len(lines) and lines[i].strip() and (
                md_render.is_table_line(lines[i]) or md_render.is_separator_line(lines[i])
            ):
                block.append(lines[i])
                i += 1
            rows = md_render.parse_pipe_table(block)
            if rows:
                for ln in md_render.ascii_table(rows, width):
                    out.append(("table", ln))
                out.append(("body", ""))
            else:
                for ln in block:
                    out.append(("table", ln.rstrip()))
            continue
        style = _markdown_line_style(raw, in_sources=in_sources)
        if style == "sources":
            in_sources = True
        elif style in ("heading", "meta") and raw.strip():
            in_sources = False
        text = md_render.display_for_tui(raw, style)
        if not text:
            i += 1
            continue
        break_long = "http" in text or "tinyurl.com" in text
        if len(text) <= width:
            wrapped = [text]
        else:
            wrapped = textwrap.wrap(
                text,
                width=width,
                break_long_words=break_long,
                break_on_hyphens=False,
            ) or [text]
        for ln in wrapped:
            out.append((style, ln.rstrip()))
        i += 1
    return out or [("body", "(empty)")]


def _chat_think_turn(fn: Callable[[], str], on_tick: Callable[[int], None]) -> str:
    stop = threading.Event()
    err: list[BaseException | None] = [None]
    out: list[str | None] = [None]

    def _spin() -> None:
        i = 0
        while not stop.is_set():
            on_tick(i)
            i += 1
            stop.wait(0.08)

    spinner = threading.Thread(target=_spin, daemon=True)
    spinner.start()
    try:
        out[0] = fn()
    except BaseException as exc:
        err[0] = exc
    finally:
        stop.set()
        spinner.join(timeout=0.3)
    if err[0] is not None:
        return f"error: {err[0]}"
    return out[0] or "(empty)"


_CHAT_INPUT_TIMEOUT_MS = 400
_CHAT_STREAM_MIN_S = 0.055
_VOICE_SPEAK_DOTS = (".", "..", "...")
_VOICE_SPEAK_DOT_S = 0.75
_CHAT_ROLE_NAME_W = max(len("you"), len("mindos"), len("mindos [thinking]"))


def _chat_role_prefix(role: str, *, wide: bool = True) -> str:
    if role == "you":
        name = "you"
    elif role == "thinking":
        name = "mindos [thinking]"
    else:
        name = "mindos"
    if wide:
        return f"{name:>{_CHAT_ROLE_NAME_W}} › "
    return f"{name} › "


_CHAT_INPUT_PREFIX = _chat_role_prefix("you", wide=False)
_CHAT_ROLE_COL = len(_chat_role_prefix("thinking"))


def _chat_save_term():
    import termios

    try:
        return termios.tcgetattr(sys.stdin.fileno())
    except (termios.error, OSError, ValueError):
        return None


def _chat_term_no_flow(old) -> None:
    if old is None:
        return
    import termios

    attrs = termios.tcgetattr(sys.stdin.fileno())
    attrs[0] &= ~(termios.IXON | termios.IXOFF)
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, attrs)


def _chat_term_restore(old) -> None:
    if old is None:
        return
    import termios

    try:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old)
    except (termios.error, OSError):
        pass


def _drop_esc_csi(stdscr, *, restore_ms: int = _CHAT_INPUT_TIMEOUT_MS) -> None:
    stdscr.timeout(25)
    second = stdscr.getch()
    if second == ord("["):
        while True:
            ch = stdscr.getch()
            if ch == -1 or (64 <= ch <= 126):
                break
    stdscr.timeout(restore_ms)


_ENTER_CSI_CODES = frozenset({13, 343, 57413, 57610, 57366, 198, 200})


def _csi_modifier_has_shift(mod: int) -> bool:
    if mod == 2:
        return True
    if mod <= 0:
        return False
    return bool((mod - 1) & 1)


def _csi_modifier_has_ctrl(mod: int) -> bool:
    if mod <= 0:
        return False
    return bool((mod - 1) & 4)


def _parse_enter_csi(body: str) -> tuple[int, int] | None:
    """Parse CSI params for Enter-ish keys (kitty u, xterm ~, fn-style letter ends)."""
    m = re.match(r"^(\d+);(\d+)([u~])$", body)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+);(\d+);(\d+)([~u])$", body)
    if m:
        return int(m.group(3)), int(m.group(2))
    m = re.match(r"^(\d+);(\d+)([A-Za-z])$", body)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+)([~u])$", body)
    if m:
        return int(m.group(1)), 1
    m = re.match(r"^27;(\d+);(\d+)([~u])$", body)
    if m:
        return int(m.group(2)), int(m.group(1))
    return None


def _is_enter_keycode(code: int) -> bool:
    return code in _ENTER_CSI_CODES or code in (10, 13)


def _read_esc_csi_enter(
    stdscr, *, restore_ms: int = _CHAT_INPUT_TIMEOUT_MS
) -> str | None:
    """After ESC: 'shift_enter', 'enter', or 'ignored' (always consumes the sequence)."""
    stdscr.timeout(50)
    second = stdscr.getch()
    if second == -1:
        stdscr.timeout(restore_ms)
        return "ignored"
    if second != ord("["):
        stdscr.timeout(restore_ms)
        return "ignored"
    body: list[str] = []
    while len(body) < 32:
        ch = stdscr.getch()
        if ch == -1:
            break
        body.append(chr(ch))
        if 64 <= ch <= 126:
            break
    stdscr.timeout(restore_ms)
    parsed = _parse_enter_csi("".join(body))
    if not parsed:
        return "ignored"
    code, mod = parsed
    if not _is_enter_keycode(code):
        return "ignored"
    if _csi_modifier_has_shift(mod) and not _csi_modifier_has_ctrl(mod):
        return "shift_enter"
    if mod in (0, 1) or not _csi_modifier_has_ctrl(mod):
        return "enter"
    return "ignored"


_NAV_CSI_ENDS = {
    "A": "up",
    "B": "down",
    "C": "right",
    "D": "left",
    "H": "home",
    "F": "end",
}


def _parse_nav_csi(body: str) -> str | None:
    if body in _NAV_CSI_ENDS:
        return _NAV_CSI_ENDS[body]
    if body.startswith("5") and body.endswith("~"):
        return "ppage"
    if body.startswith("6") and body.endswith("~"):
        return "npage"
    if body.startswith("1") and body.endswith("~"):
        return "home"
    if body.startswith("4") and body.endswith("~"):
        return "end"
    return None


def _read_esc_csi_body(stdscr) -> str:
    """Read bytes after ESC [ (caller consumed ESC and [)."""
    body: list[str] = []
    while len(body) < 32:
        ch = stdscr.getch()
        if ch == -1:
            break
        body.append(chr(ch))
        if 64 <= ch <= 126:
            break
    return "".join(body)


def _read_esc_csi_action(
    stdscr, *, restore_ms: int = _CHAT_INPUT_TIMEOUT_MS
) -> str:
    """shift_enter | enter | up | down | left | right | ppage | npage | home | end | bare_esc | ignored."""
    stdscr.timeout(120)
    second = stdscr.getch()
    if second == -1:
        stdscr.timeout(restore_ms)
        return "bare_esc"
    if second != ord("["):
        stdscr.timeout(restore_ms)
        return "ignored"
    raw = _read_esc_csi_body(stdscr)
    stdscr.timeout(restore_ms)
    parsed = _parse_enter_csi(raw)
    if parsed:
        code, mod = parsed
        if _is_enter_keycode(code):
            if _csi_modifier_has_shift(mod) and not _csi_modifier_has_ctrl(mod):
                return "shift_enter"
            if mod in (0, 1) or not _csi_modifier_has_ctrl(mod):
                return "enter"
    nav = _parse_nav_csi(raw)
    if nav:
        return nav
    return "ignored"


def _chat_cursor_logical(buf: str, cursor: int) -> tuple[int, int]:
    cursor = max(0, min(cursor, len(buf)))
    line = buf[:cursor].count("\n")
    last_nl = buf.rfind("\n", 0, cursor)
    col = cursor if last_nl == -1 else cursor - last_nl - 1
    return line, col


def _chat_cursor_from_logical(buf: str, line: int, col: int) -> int:
    parts = buf.split("\n") if buf else [""]
    line = max(0, min(line, len(parts) - 1))
    col = max(0, min(col, len(parts[line])))
    off = sum(len(parts[i]) + 1 for i in range(line))
    return off + col


_CARET_SENTINEL = "\x00"


def _chat_wrap_line(line: str, wrap_w: int) -> list[str]:
    if not line:
        return [""]
    chunks = textwrap.wrap(
        line,
        width=wrap_w,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return chunks if chunks else [""]


def _chat_wrap_line_with_caret(
    line: str, col: int, wrap_w: int, mark: str
) -> list[str]:
    col = max(0, min(col, len(line)))
    virt = line[:col] + _CARET_SENTINEL + line[col:]
    chunks = textwrap.wrap(
        virt,
        width=wrap_w,
        break_long_words=True,
        break_on_hyphens=False,
    )
    if not chunks:
        chunks = [_CARET_SENTINEL]
    return [c.replace(_CARET_SENTINEL, mark) for c in chunks]


def _chat_chunk_cursor(line: str, col: int, wrap_w: int) -> tuple[int, int]:
    chunks = _chat_wrap_line(line, wrap_w)
    walked = 0
    for i, chunk in enumerate(chunks):
        if col <= walked + len(chunk):
            return i, col - walked
        walked += len(chunk)
    last = chunks[-1] if chunks else ""
    return max(0, len(chunks) - 1), len(last)


def chat_input_lines(
    buf: str,
    cursor: int,
    inner_w: int,
    *,
    prefix: str = _CHAT_INPUT_PREFIX,
    show_cursor: bool = True,
) -> list[str]:
    """Wrap draft text and place the caret at `cursor` (supports newlines + soft wraps)."""
    cont_pad = " " * len(prefix)
    wrap_w = max(4, inner_w - len(prefix))
    mark = "▌" if show_cursor else " "
    logical = buf.split("\n") if buf else []
    cur_line, cur_col = _chat_cursor_logical(buf, cursor)

    rows: list[str] = []
    first = True
    if not logical:
        return [f"{prefix}{mark}"]

    for li, log_line in enumerate(logical):
        if li == cur_line:
            chunks = _chat_wrap_line_with_caret(log_line, cur_col, wrap_w, mark)
        else:
            chunks = _chat_wrap_line(log_line, wrap_w)
        for piece in chunks:
            head = prefix if first else cont_pad
            rows.append(head + piece)
            first = False
    return rows


def chat_conversation(
    turn: Callable[[str], str],
    *,
    initial_history: list[tuple[str, str]] | None = None,
    list_history: Callable[[], list] | None = None,
    load_history: Callable[[str], list[tuple[str, str]] | None] | None = None,
    delete_history: Callable[[str], bool] | None = None,
) -> None:
    """Continuous assistant chat; top bar Back / History (←/→, Enter on empty input)."""
    import curses as c

    stdscr = _tui_stdscr
    if stdscr is None:
        return

    saved_term = _chat_save_term()
    _chat_term_no_flow(saved_term)
    try:
        _chat_conversation_loop(
            turn,
            stdscr=stdscr,
            initial_history=initial_history,
            list_history=list_history,
            load_history=load_history,
            delete_history=delete_history,
        )
    finally:
        _chat_term_restore(saved_term)
        try:
            voice_stt.shutdown()
        except Exception:
            pass


def _chat_conversation_loop(
    turn: Callable[..., str],
    *,
    stdscr,
    initial_history: list[tuple[str, str]] | None,
    list_history: Callable[[], list] | None,
    load_history: Callable[[str], list[tuple[str, str]] | None],
    delete_history: Callable[[str], bool] | None = None,
) -> None:
    import curses as c

    normal, sel_attr = _curses_harvest(stdscr)
    user_attr = sel_attr
    asst_attr = normal
    think_attr = user_attr
    tab_norm = normal
    tab_sel = sel_attr
    voice_attr = sel_attr
    try:
        think_attr = asst_attr | c.A_DIM
    except c.error:
        pass
    if c.has_colors():
        try:
            asst_attr = _curses_bright_white(c, 3)
            think_attr = asst_attr | c.A_DIM
        except c.error:
            pass
        try:
            c.init_pair(8, c.COLOR_RED, -1)
            voice_attr = c.color_pair(8) | c.A_BOLD
        except c.error:
            voice_attr = c.A_BOLD
        try:
            # 256-color harvest orange (xterm 208); muted grey for inactive tab
            c.init_pair(6, 208, -1)
            c.init_pair(7, 252, -1)
            tab_norm = c.color_pair(7)
            tab_sel = c.color_pair(6) | c.A_BOLD
        except c.error:
            try:
                c.init_pair(6, c.COLOR_YELLOW, -1)
                tab_norm = normal | c.A_DIM
                tab_sel = c.color_pair(6) | c.A_BOLD
            except c.error:
                pass

    md_heading = asst_attr | c.A_BOLD
    md_table = asst_attr | c.A_DIM
    md_rule = asst_attr | c.A_DIM
    md_sources = asst_attr | c.A_BOLD
    md_source_item = asst_attr | c.A_DIM
    md_meta = tab_norm
    md_list = asst_attr
    md_attr = {
        "body": asst_attr,
        "heading": md_heading,
        "table": md_table,
        "rule": md_rule,
        "list": md_list,
        "sources": md_sources,
        "source_item": md_source_item,
        "meta": md_meta,
    }

    _TOP_TABS = ("Back", "History")
    history: list[tuple[str, str]] = list(initial_history or [])
    scroll = 0
    follow_tail = bool(history)
    buf = ""
    cursor = 0
    input_focus = True
    top_tab = 0
    thinking_line: str | None = None
    frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _render_sig: tuple[int, int, str | None] | None = None
    _rendered_lines: list[tuple[int, str]] = []

    voice_preview_q: SimpleQueue[str] = SimpleQueue()
    voice_listening = False
    voice_anchor = 0
    voice_started_at = 0.0
    voice_err: list[str] = []

    def _voice_busy() -> bool:
        return voice_listening

    def _voice_apply_live(text: str) -> None:
        nonlocal buf, cursor, voice_anchor
        text = text.strip().replace("\n", " ")
        if not text:
            return
        room = max(0, 4000 - voice_anchor)
        text = text[:room]
        prior = buf[voice_anchor:cursor]
        if prior and len(text) < len(prior) and prior.startswith(text):
            return
        buf = buf[:voice_anchor] + text + buf[cursor:]
        cursor = voice_anchor + len(text)

    def _voice_drain_preview() -> None:
        nonlocal thinking_line
        latest: str | None = None
        while True:
            try:
                latest = voice_preview_q.get_nowait()
            except Empty:
                break
        if not latest:
            return
        _voice_apply_live(latest)
        thinking_line = None

    def _voice_stop() -> None:
        nonlocal voice_listening, thinking_line
        if not voice_listening:
            return
        voice_listening = False
        try:
            voice_stt.stop_listen()
        except Exception as exc:
            voice_err.append(str(exc).split("\n", 1)[0][:120])
        _voice_drain_preview()
        if voice_err:
            thinking_line = f"  voice: {voice_err[0][:100]}"
            voice_err.clear()

    def _voice_start() -> None:
        nonlocal voice_listening, voice_anchor, thinking_line, voice_started_at
        if _voice_busy():
            return
        if thinking_line and "Thinking" in thinking_line:
            return
        ok, msg = voice_stt.stt_status()
        if not ok:
            thinking_line = f"  {msg}"
            _draw(show_cursor=bool(buf))
            return
        voice_anchor = cursor
        try:
            if not voice_stt.is_warmed():
                thinking_line = "  voice · loading model (cached on disk)…"
                _draw(show_cursor=bool(buf))
            voice_stt.warm()

            def on_partial(t: str) -> None:
                voice_preview_q.put(t)

            voice_stt.start_listen(on_partial)
            voice_listening = True
            voice_started_at = time.monotonic()
            thinking_line = None
        except Exception as exc:
            thinking_line = f"  voice: {str(exc).split(chr(10), 1)[0][:100]}"
        _draw(show_cursor=bool(buf))

    def _voice_poll() -> None:
        if voice_listening:
            _voice_drain_preview()
            _draw(show_cursor=True)

    def _flat_history(inner_w: int) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for role, text in history:
            prefix = _chat_role_prefix(role)
            wrap_w = max(20, inner_w - len(prefix) - 2)
            cont_pad = " " * len(prefix)
            if role == "you":
                for i, ln in enumerate(textwrap.wrap(text, width=wrap_w) or ["(empty)"]):
                    pad = prefix if i == 0 else cont_pad
                    out.append((user_attr, f"{pad}{ln}"))
                continue
            if role == "thinking":
                lines = _thinking_chat_lines(text, wrap_w)
                for i, (_style, ln) in enumerate(lines):
                    pad = prefix if i == 0 else cont_pad
                    out.append((think_attr, f"{pad}{ln}"))
                continue
            lines = _markdown_chat_lines(text, wrap_w)
            for i, (style, ln) in enumerate(lines):
                pad = prefix if i == 0 else cont_pad
                out.append((md_attr.get(style, asst_attr), f"{pad}{ln}"))
        return out

    def _rendered_transcript(inner_w: int) -> list[tuple[int, str]]:
        nonlocal _render_sig, _rendered_lines
        think = thinking_line or ""
        sig = (len(history), inner_w, think)
        if sig != _render_sig:
            _rendered_lines = _flat_history(inner_w)
            if thinking_line:
                _rendered_lines.append((think_attr, thinking_line))
            _render_sig = sig
        return _rendered_lines

    def _layout(inner_w: int) -> tuple[int, int, int]:
        h, w = stdscr.getmaxyx()
        _ = w
        box_bottom = h - 1
        transcript_top = 5
        text_w = max(8, inner_w - 2)
        max_input_rows = max(1, box_bottom - transcript_top - 1)
        input_lines = chat_input_lines(
            buf, cursor, text_w, show_cursor=False, prefix=_CHAT_INPUT_PREFIX
        )
        if len(input_lines) > max_input_rows:
            drop = len(input_lines) - max_input_rows + 1
            input_lines = [
                f"{' ' * len(_CHAT_INPUT_PREFIX)}… +{drop} line(s) above"
            ] + input_lines[-(max_input_rows - 1) :]
        input_start = box_bottom - len(input_lines)
        input_box_top = input_start - 1
        transcript_h = max(1, input_box_top - transcript_top)
        rendered = _rendered_transcript(inner_w)
        max_scroll = max(0, len(rendered) - transcript_h)
        return transcript_h, max_scroll, input_start

    def _transcript_metrics() -> tuple[int, int]:
        h, w = stdscr.getmaxyx()
        inner_w = max(10, w - 2)
        th, max_sc, _ = _layout(inner_w)
        return th, max_sc

    def _pin_scroll(max_sc: int) -> None:
        nonlocal scroll, follow_tail
        if follow_tail:
            scroll = max_sc
        else:
            scroll = max(0, min(scroll, max_sc))
            if scroll >= max_sc:
                follow_tail = True
                scroll = max_sc

    def _editing_draft() -> bool:
        return input_focus and bool(buf.strip())

    def _apply_nav(action: str) -> None:
        nonlocal scroll, cursor, top_tab, follow_tail
        th, max_sc = _transcript_metrics()
        step = max(1, th - 1)
        if action == "ppage":
            follow_tail = False
            scroll = max(0, scroll - step)
            return
        if action == "npage":
            follow_tail = False
            scroll = min(max_sc, scroll + step)
            if scroll >= max_sc:
                follow_tail = True
            return
        if action == "home":
            if _editing_draft():
                cursor = 0
            else:
                follow_tail = False
                scroll = 0
            return
        if action == "end":
            if _editing_draft():
                cursor = len(buf)
            else:
                follow_tail = True
                scroll = max_sc
            return
        if action == "left":
            if _editing_draft():
                cursor = max(0, cursor - 1)
            else:
                top_tab = (top_tab - 1) % len(_TOP_TABS)
            return
        if action == "right":
            if _editing_draft():
                cursor = min(len(buf), cursor + 1)
            else:
                top_tab = (top_tab + 1) % len(_TOP_TABS)
            return
        if action == "up":
            if _editing_draft():
                line, col = _chat_cursor_logical(buf, cursor)
                if line > 0:
                    cursor = _chat_cursor_from_logical(buf, line - 1, col)
            else:
                follow_tail = False
                scroll = max(0, scroll - 1)
            return
        if action == "down":
            if _editing_draft():
                line, col = _chat_cursor_logical(buf, cursor)
                max_line = buf.count("\n")
                if line < max_line:
                    cursor = _chat_cursor_from_logical(buf, line + 1, col)
            else:
                follow_tail = False
                scroll = min(max_sc, scroll + 1)
                if scroll >= max_sc:
                    follow_tail = True
            return

    def _scroll_key_to_action(key: int) -> str | None:
        import curses as c

        if key in (c.KEY_UP, ord("k")):
            return "up"
        if key in (c.KEY_DOWN, ord("j")):
            return "down"
        if key in (c.KEY_PPAGE,):
            return "ppage"
        if key in (c.KEY_NPAGE,):
            return "npage"
        return None

    def _burst_transcript_scroll(first: str, *, restore_ms: int) -> None:
        """Apply repeated scroll keys in one draw (terminal key-repeat / fast taps)."""
        import curses as c

        actions = [first]
        stdscr.timeout(0)
        while True:
            k = stdscr.getch()
            if k == -1:
                break
            act = _scroll_key_to_action(k)
            if act is None:
                try:
                    c.ungetch(k)
                except c.error:
                    pass
                break
            actions.append(act)
        stdscr.timeout(restore_ms)
        net = 0
        page = 0
        th, max_sc = _transcript_metrics()
        step = max(1, th - 1)
        for act in actions:
            if act == "up":
                net -= 1
            elif act == "down":
                net += 1
            elif act == "ppage":
                page -= step
            elif act == "npage":
                page += step
        nonlocal scroll, follow_tail
        if not _editing_draft() and (net or page):
            follow_tail = False
            scroll = max(0, min(max_sc, scroll + net + page))
            if scroll >= max_sc:
                follow_tail = True
                scroll = max_sc

    def _open_history_tab() -> None:
        """Empty Enter on History tab: open session picker."""
        nonlocal history, scroll, thinking_line, follow_tail, _render_sig
        try:
            if list_history is None or load_history is None:
                thinking_line = "  (history unavailable)"
                return
            try:
                rows = list_history()
            except Exception as exc:
                msg = str(exc).split("\n", 1)[0][:120]
                if "chat_sessions" in msg and (
                    "exist" in msg.lower() or "undefined" in msg.lower()
                ):
                    msg = "chat_sessions missing — run: uv run alembic upgrade head"
                thinking_line = f"  history error: {msg}"
                return
            sessions: list[tuple[str, str, str]] = []
            for row in rows:
                if len(row) >= 3:
                    sessions.append((row[0], row[1], row[2]))
                elif len(row) == 2:
                    sessions.append((row[0], row[1], ""))
            if not sessions:
                thinking_line = "  (no saved sessions yet)"
                return
            stdscr.timeout(-1)
            try:
                picked = _session_menu_draw(
                    stdscr, sessions, delete_session=delete_history
                )
            except Exception as exc:
                thinking_line = f"  history error: {exc}"
                picked = None
            try:
                c.flushinp()
            except c.error:
                pass
            stdscr.timeout(_CHAT_INPUT_TIMEOUT_MS)
            if picked is None:
                thinking_line = None
                return
            try:
                loaded = load_history(picked)
            except Exception as exc:
                thinking_line = f"  load error: {exc}"
                return
            if loaded is None:
                thinking_line = "  (session not found)"
                return
            history = list(loaded)
            thinking_line = None
            follow_tail = True
            _render_sig = None
        finally:
            _draw(show_cursor=input_focus and bool(buf.strip()))

    def _pick_history() -> None:
        _open_history_tab()

    def _draw(*, show_cursor: bool = False) -> None:
        nonlocal scroll, follow_tail
        h, w = stdscr.getmaxyx()
        if h < 9 or w < 24:
            return
        pad = 1
        inner_w = max(10, w - pad * 2)
        box_w = inner_w
        text_w = max(8, box_w - 2)
        transcript_top = 5
        box_bottom = h - 1

        stdscr.erase()
        title = "mindos · chat"
        try:
            stdscr.addstr(0, pad, title[: max(0, w - pad - 1)], normal | c.A_BOLD)
        except c.error:
            pass
        tab_gap = 2
        segments: list[tuple[str, int]] = []
        for i, label in enumerate(_TOP_TABS):
            mark = "› " if i == top_tab else ""
            segments.append((i, f"{mark}{label}"))
        bar_w = sum(len(s) for _, s in segments) + tab_gap * max(0, len(segments) - 1)
        tab_x = max(pad, w - pad - bar_w)
        for i, segment in segments:
            attr = tab_sel if i == top_tab else tab_norm
            try:
                stdscr.addstr(0, tab_x, segment[: max(0, w - tab_x - pad)], attr)
            except c.error:
                pass
            tab_x += len(segment) + tab_gap
        hint_attr = tab_norm
        hint1 = (
            " Tab → Back/History  |  ←/→ tabs  |  Enter send (History · Enter = saved chats)"
            if top_tab == 1
            else " Tab → Back/History  |  ←/→ tabs  |  Enter send (Back · Enter = leave)"
        )
        hint2 = " Tab again while typing → browse  |  h = history (when not typing)  |  Ctrl+G voice"
        try:
            stdscr.addstr(1, pad, tui_theme.rule(inner_w)[:inner_w], normal)
            stdscr.addstr(2, pad, hint1[:inner_w], hint_attr)
            stdscr.addstr(3, pad, hint2[:inner_w], hint_attr)
            stdscr.addstr(4, pad, tui_theme.rule(inner_w)[:inner_w], normal)
        except c.error:
            pass
        if _voice_busy():
            dot_i = int(time.monotonic() / _VOICE_SPEAK_DOT_S) % len(_VOICE_SPEAK_DOTS)
            speak_label = "● Speak"
            dot_w = 3
            hx = max(pad, w - pad - len(speak_label) - dot_w)
            try:
                stdscr.addstr(3, hx, speak_label, voice_attr)
                stdscr.addstr(
                    3,
                    hx + len(speak_label),
                    _VOICE_SPEAK_DOTS[dot_i].ljust(dot_w),
                    voice_attr,
                )
            except c.error:
                pass

        prefix = _CHAT_INPUT_PREFIX
        input_lines = chat_input_lines(
            buf, cursor, text_w, prefix=prefix, show_cursor=show_cursor
        )

        max_input_rows = max(1, box_bottom - transcript_top - 1)
        if len(input_lines) > max_input_rows:
            drop = len(input_lines) - max_input_rows + 1
            input_lines = [
                f"{' ' * len(prefix)}… +{drop} line(s) above"
            ] + input_lines[-(max_input_rows - 1) :]

        input_start = box_bottom - len(input_lines)
        input_box_top = input_start - 1
        transcript_h = max(1, input_box_top - transcript_top)

        rendered = _rendered_transcript(inner_w)
        max_scroll = max(0, len(rendered) - transcript_h)
        _pin_scroll(max_scroll)
        for i in range(transcript_h):
            ri = scroll + i
            if ri >= len(rendered):
                break
            attr, ln = rendered[ri]
            try:
                stdscr.addstr(transcript_top + i, pad, ln[:inner_w], attr)
            except c.error:
                pass

        in_attr = asst_attr
        try:
            stdscr.addstr(input_box_top, pad, tui_theme.hline(box_w)[:box_w], normal)
            for i, ln in enumerate(input_lines):
                stdscr.addstr(
                    input_start + i,
                    pad,
                    tui_theme.boxed_row(ln, box_w)[:box_w],
                    in_attr,
                )
            stdscr.addstr(
                box_bottom,
                pad,
                tui_theme.hline(box_w)[:box_w],
                normal,
            )
        except c.error:
            pass
        stdscr.refresh()

    def _submit_turn() -> None:
        nonlocal buf, cursor, scroll, thinking_line, follow_tail, _render_sig
        if _voice_busy():
            _voice_stop()
        msg = buf.rstrip()
        if not msg:
            return
        buf = ""
        cursor = 0
        history.append(("you", msg))
        follow_tail = True
        _render_sig = None
        _draw(show_cursor=False)

        think_idx: int | None = None
        stream_idx: int | None = None
        last_paint = 0.0
        pending = ""
        compose_after_think = False

        def on_partial(text: str, phase: str = "answer") -> None:
            nonlocal thinking_line, think_idx, stream_idx, last_paint, _render_sig, pending, compose_after_think
            pending = text
            now = time.monotonic()
            if phase == "think":
                compose_after_think = True
                if think_idx is None:
                    history.append(("thinking", text))
                    think_idx = len(history) - 1
                else:
                    history[think_idx] = ("thinking", text)
                _render_sig = None
                follow_tail = True
                _draw(show_cursor=False)
                return
            compose_after_think = False
            thinking_line = None
            if stream_idx is None:
                history.append(("assistant", text))
                stream_idx = len(history) - 1
                last_paint = now
                _render_sig = None
                follow_tail = True
                _draw(show_cursor=False)
                return
            if now - last_paint < _CHAT_STREAM_MIN_S:
                return
            last_paint = now
            history[stream_idx] = ("assistant", text)
            _render_sig = None
            follow_tail = True
            _draw(show_cursor=False)

        stop = threading.Event()
        err: list[BaseException | None] = [None]
        answer_box: list[str | None] = [None]

        def _run_turn() -> None:
            try:
                answer_box[0] = turn(msg, on_partial)
            except TypeError:
                answer_box[0] = turn(msg)
            except BaseException as exc:
                err[0] = exc
            finally:
                stop.set()

        def _spin() -> None:
            nonlocal thinking_line, compose_after_think
            i = 0
            while not stop.is_set():
                if thinking_line is not None:
                    if compose_after_think:
                        pfx = _chat_role_prefix("mindos")
                        tail = " composing answer…"
                    else:
                        pfx = _chat_role_prefix("thinking")
                        tail = " …"
                    thinking_line = f"{pfx}{frames[i % len(frames)]}{tail}"
                    _draw(show_cursor=False)
                i += 1
                stop.wait(0.08)

        compose_after_think = False
        thinking_line = f"{_chat_role_prefix('thinking')}{frames[0]} …"
        spinner = threading.Thread(target=_spin, daemon=True)
        spinner.start()
        _run_turn()
        spinner.join(timeout=0.3)
        thinking_line = None
        if err[0] is not None:
            answer = f"error: {err[0]}"
        else:
            answer = answer_box[0] or "(empty)"

        plan, rest = _split_plan_prefix(answer)
        if plan:
            merged = plan if think_idx is None else f"{history[think_idx][1]}\n\n{plan}".strip()
            if think_idx is None:
                history.append(("thinking", merged))
                think_idx = len(history) - 1
            else:
                history[think_idx] = ("thinking", merged)
            answer = rest if rest.strip() else answer

        if stream_idx is None:
            history.append(("assistant", answer))
        else:
            history[stream_idx] = ("assistant", answer)
        follow_tail = True
        _render_sig = None
        _draw(show_cursor=False)

    def _insert_newline() -> None:
        nonlocal buf, cursor
        if len(buf) >= 4000:
            return
        buf = buf[:cursor] + "\n" + buf[cursor:]
        cursor += 1
        _draw(show_cursor=True)

    c.curs_set(0)
    stdscr.keypad(True)
    if hasattr(stdscr, "meta"):
        stdscr.meta(True)
    blink = False
    while True:
        blink_idle = follow_tail and not buf and thinking_line is None and input_focus
        voice_waiting = _voice_busy()
        if voice_waiting:
            stdscr.timeout(120)
        elif blink_idle:
            stdscr.timeout(_CHAT_INPUT_TIMEOUT_MS)
        else:
            stdscr.timeout(-1)
        show_caret = input_focus and (bool(buf) or (blink_idle and blink))
        _draw(show_cursor=show_caret)
        key = stdscr.getch()
        _voice_poll()
        if key == -1:
            if blink_idle or voice_waiting:
                blink = not blink
            continue
        if _voice_busy() and key == 27:
            _voice_stop()
            _draw(show_cursor=show_caret)
            continue
        if key == 7:
            if _voice_busy():
                _voice_stop()
            else:
                _voice_start()
            continue
        if key == 9:
            if _editing_draft():
                input_focus = not input_focus
                if not input_focus:
                    follow_tail = False
            else:
                top_tab = (top_tab + 1) % len(_TOP_TABS)
            _draw(show_cursor=input_focus and bool(buf.strip()))
            continue
        if key == 27:
            kind = _read_esc_csi_action(stdscr)
            if kind == "shift_enter" and input_focus and buf.strip():
                _insert_newline()
            elif kind == "enter":
                if input_focus and buf.strip():
                    _submit_turn()
                elif top_tab == 0:
                    stdscr.timeout(-1)
                    return
                elif top_tab == 1:
                    _open_history_tab()
            elif kind == "bare_esc":
                stdscr.timeout(-1)
                return
            elif kind in ("up", "down", "ppage", "npage") and not _editing_draft():
                _burst_transcript_scroll(
                    kind, restore_ms=-1 if not blink_idle else _CHAT_INPUT_TIMEOUT_MS
                )
            elif kind not in ("ignored", "enter", "shift_enter"):
                _apply_nav(kind)
            _draw(show_cursor=input_focus and (bool(buf) or (blink_idle and blink)))
            continue
        scroll_act = _scroll_key_to_action(key)
        if scroll_act and not _editing_draft():
            restore = -1 if not blink_idle else _CHAT_INPUT_TIMEOUT_MS
            _burst_transcript_scroll(scroll_act, restore_ms=restore)
            _draw(show_cursor=input_focus and blink_idle and blink)
            continue
        if key in (c.KEY_LEFT, c.KEY_RIGHT, c.KEY_UP, c.KEY_DOWN, c.KEY_PPAGE, c.KEY_NPAGE):
            if key == c.KEY_PPAGE:
                _apply_nav("ppage")
            elif key == c.KEY_NPAGE:
                _apply_nav("npage")
            elif key == c.KEY_LEFT:
                _apply_nav("left")
            elif key == c.KEY_RIGHT:
                _apply_nav("right")
            elif key == c.KEY_UP:
                _apply_nav("up")
            elif key == c.KEY_DOWN:
                _apply_nav("down")
            _draw(show_cursor=input_focus and (bool(buf) or (blink_idle and blink)))
            continue
        if key in (c.KEY_HOME, 1):
            _apply_nav("home")
            _draw(show_cursor=input_focus and (bool(buf) or (blink_idle and blink)))
            continue
        if key in (c.KEY_END, 5):
            _apply_nav("end")
            _draw(show_cursor=input_focus and (bool(buf) or (blink_idle and blink)))
            continue
        if key in (458, 459):
            if not input_focus or not buf:
                continue
            _insert_newline()
            continue
        if key in (c.KEY_ENTER, 10, 13, 343, ord("\n")):
            if input_focus and buf.strip():
                _submit_turn()
                continue
            if top_tab == 0:
                stdscr.timeout(-1)
                return
            if top_tab == 1:
                _open_history_tab()
                continue
            _draw(show_cursor=input_focus)
            continue
        if key in (ord("h"), ord("H")) and not input_focus:
            top_tab = 1
            _open_history_tab()
            continue
        if not input_focus:
            if 32 <= key <= 126 and len(buf) < 4000:
                input_focus = True
            else:
                continue
        if key in (
            c.KEY_BACKSPACE,
            127,
            263,
            259,
            510,
            8,
        ):
            if cursor > 0:
                buf = buf[: cursor - 1] + buf[cursor:]
                cursor -= 1
            _draw(show_cursor=True)
            continue
        if key in (c.KEY_DC, 330) and cursor < len(buf):
            buf = buf[:cursor] + buf[cursor + 1 :]
            _draw(show_cursor=True)
            continue
        if 32 <= key <= 126 and len(buf) < 4000:
            buf = buf[:cursor] + chr(key) + buf[cursor:]
            cursor += 1
            _draw(show_cursor=True)


def prompt() -> str:
    if _RICH and _CONSOLE:
        return _CONSOLE.input("[bold cyan]mindos[/] [dim]›[/] ").strip()
    sys.stdout.write("mindos › ")
    sys.stdout.flush()
    return sys.stdin.readline().strip()
