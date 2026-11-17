from cli.md_render import ascii_table, display_for_tui, parse_pipe_table, rich_chat_span_lines


def test_list_keeps_indent_and_marker():
    assert display_for_tui("  - nested item", "list") == "  • nested item"
    assert display_for_tui("1. first", "list") == "1. first"


def test_heading_and_note_stripped():
    assert display_for_tui("### Top Gmail Messages", "heading") == "Top Gmail Messages"
    assert display_for_tui("**Summary**", "heading") == "Summary"
    assert (
        display_for_tui("*(Note: only 10 messages)*", "meta") == "Note: only 10 messages"
    )


def test_rich_chat_span_lines_heading_without_hashes() -> None:
    rows = rich_chat_span_lines("### Summary\n\nPlain line.", 60)
    assert rows is not None
    assert rows[0][0] == "heading"
    assert rows[0][1] == "Summary"
    assert "###" not in rows[0][1]


def test_rich_chat_span_lines_bold_label() -> None:
    rows = rich_chat_span_lines("**When:** Tue 3pm", 60)
    assert rows is not None
    row = rows[0]
    assert row[1].startswith("When:")
    assert len(row) == 3
    keys = [k for k, _ in row[2]]
    assert "strong" in keys


def test_rich_skipped_for_indented_drive_links() -> None:
    text = "• file\n  https://drive.google.com/x"
    assert rich_chat_span_lines(text, 60) is None


def test_meta_strips_inline_heading_marks() -> None:
    raw = "**Source(s):** ### Attachment: foo.pdf Part 1"
    assert display_for_tui(raw, "meta") == "Source(s): Attachment: foo.pdf Part 1"


def test_meta_normalizes_legacy_source_excerpt_label() -> None:
    raw = "**Source excerpt:** quote here"
    assert display_for_tui(raw, "meta") == "Source(s): quote here"


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