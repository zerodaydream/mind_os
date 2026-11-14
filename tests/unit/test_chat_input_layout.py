"""Chat draft caret + wrap layout (terminal input)."""

from cli.term import (
    _chat_chunk_cursor,
    _chat_cursor_from_logical,
    _chat_cursor_logical,
    _csi_modifier_has_shift,
    _is_enter_keycode,
    _parse_enter_csi,
    chat_input_lines,
)


def test_cursor_logical_multiline():
    buf = "ab\ncd"
    assert _chat_cursor_logical(buf, 0) == (0, 0)
    assert _chat_cursor_logical(buf, 2) == (0, 2)
    assert _chat_cursor_logical(buf, 3) == (1, 0)
    assert _chat_cursor_logical(buf, 5) == (1, 2)


def test_cursor_round_trip():
    buf = "hello\nworld"
    for line, col in ((0, 3), (1, 0), (1, 5)):
        idx = _chat_cursor_from_logical(buf, line, col)
        assert _chat_cursor_logical(buf, idx) == (line, col)


def test_caret_mid_word_after_newlines():
    buf = "So i want\nlook at"
    cursor = len("So i want\nlo")
    lines = chat_input_lines(buf, cursor, 60, show_cursor=True)
    joined = "\n".join(lines)
    assert "▌" in joined
    assert joined.index("▌") == joined.index("lo") + 2


def test_wrap_soft_break_cursor():
    buf = "a" * 30
    cursor = 15
    wrap_w = 10
    pi, pin = _chat_chunk_cursor(buf, cursor, wrap_w)
    assert pi == 1
    assert pin == 5
    lines = chat_input_lines(buf, cursor, len("you › ") + wrap_w, show_cursor=True)
    assert any("▌" in ln for ln in lines)


def test_csi_shift_enter_variants():
    assert _parse_enter_csi("13;2u") == (13, 2)
    assert _parse_enter_csi("13;2~") == (13, 2)
    assert _parse_enter_csi("27;2;13~") == (13, 2)
    assert _parse_enter_csi("13;2E") == (13, 2)
    assert _parse_enter_csi("13~") == (13, 1)
    assert _parse_enter_csi("27;1;13~") == (13, 1)
    assert _csi_modifier_has_shift(2)  # xterm + kitty shift (1+1)
    assert _is_enter_keycode(13)
    assert _is_enter_keycode(10)


def test_caret_wrap_stable_blink_off():
    buf = "x" * 9 + " "
    cursor = len(buf)
    inner = len("you › ") + 10
    on = chat_input_lines(buf, cursor, inner, show_cursor=True)
    off = chat_input_lines(buf, cursor, inner, show_cursor=False)
    assert len(on) == len(off)
    assert " " in "".join(on).replace("▌", " ")


def test_backspace_cursor_math():
    buf = "abc"
    cursor = 2
    new_buf = buf[: cursor - 1] + buf[cursor:]
    assert new_buf == "ac"
    assert _chat_cursor_logical(new_buf, 1) == (0, 1)
