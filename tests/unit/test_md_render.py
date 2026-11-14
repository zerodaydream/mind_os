from cli.md_render import ascii_table, display_for_tui, parse_pipe_table


def test_list_keeps_indent_and_marker():
    assert display_for_tui("  - nested item", "list") == "  - nested item"
    assert display_for_tui("1. first", "list") == "1. first"


def test_heading_and_note_stripped():
    assert display_for_tui("### Top Gmail Messages", "heading") == "Top Gmail Messages"
    assert (
        display_for_tui("*(Note: only 10 messages)*", "meta") == "Note: only 10 messages"
    )


def test_ascii_table_fits_width():
    block = [
        "| C1 | C2 |",
        "| --- | --- |",
        "| **R1** | 0 |",
        "| R2 | 1 |",
    ]
    rows = parse_pipe_table(block)
    lines = ascii_table(rows, width=40)
    assert lines[0].startswith("+")
    assert "R1" in "\n".join(lines)
    assert max(len(ln) for ln in lines) <= 40