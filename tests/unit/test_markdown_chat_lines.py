from cli.term import _markdown_chat_lines, _markdown_line_style


def test_sources_block_styles():
    assert _markdown_line_style("sources:", in_sources=False) == "sources"
    assert _markdown_line_style("  1. [gmail] Hi", in_sources=True) == "source_item"


def test_table_and_heading():
    assert _markdown_line_style("| A | B |", in_sources=False) == "table"
    assert _markdown_line_style("## Roles", in_sources=False) == "heading"


def test_blank_lines_preserved():
    text = "para one\n\npara two"
    lines = _markdown_chat_lines(text, width=60)
    assert any(ln == "" for _, ln in lines)


def test_long_source_line_wraps_not_truncates():
    long = "  1. [gmail] " + ("Indeed Application: AI Engineer – " * 6)
    lines = _markdown_chat_lines(long, width=60)
    assert len(lines) > 1
    assert "…" not in "".join(ln for _, ln in lines)


def test_long_file_path_wraps_not_hard_cut():
    path = "/Users/me/Downloads/Halsey_-_I_Never_Loved_You_Official_Music_Video.m4v"
    body = f"Saved to {path}"
    lines = _markdown_chat_lines(body, width=50)
    joined = "".join(ln for _, ln in lines)
    assert "Official" in joined
    assert path not in joined or len(lines) > 1