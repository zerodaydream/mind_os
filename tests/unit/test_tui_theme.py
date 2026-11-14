from cli import tui_theme


def test_two_col_table_ascii_and_even_width():
    lines = tui_theme.two_col_table([("sync", "Gmail")], width=50, selected=0)
    assert lines[0].startswith("+")
    assert all(len(ln) == len(lines[0]) for ln in lines)


def test_menu_panel_sizes_from_content():
    rows = [("sync:process", "Sync Gmail and process outbox")]
    lines = tui_theme.menu_panel("mindos", rows, selected=0, subtitle="project: /x")
    assert "sync:process" in "".join(lines)
    assert all(len(ln) == len(lines[0]) for ln in lines)


def test_sink_uses_terminal_width():
    lines = tui_theme.sink_command_rows([("chat", "Ask")])
    assert lines[0][0] == "+"


def test_boxed_row_matches_hline_width():
    w = 40
    assert len(tui_theme.boxed_row("you › hi", w)) == w
    assert len(tui_theme.hline(w)) == w
