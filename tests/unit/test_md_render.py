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


def test_ascii_table_wraps_long_cells() -> None:
    rows = [
        ["To", "Subject"],
        [
            "krishna priya <kpriya1004@gmail.com>, a.subramanyam <Asm.yam@gmail.com>",
            "Chin Prop Final 13 Nov 26 & Old docs",
        ],
    ]
    lines = ascii_table(rows, width=72)
    joined = "\n".join(lines)
    assert "kpriya1004" in joined
    assert "Chin Prop" in joined
    assert "Asm.yam" in joined