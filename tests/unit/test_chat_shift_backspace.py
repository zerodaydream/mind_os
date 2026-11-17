from cli.term import _chat_shift_backspace_sweep, _word_before_cursor


def test_word_before_cursor() -> None:
    assert _word_before_cursor("hello world", 11) == 6
    assert _word_before_cursor("hello world", 5) == 0


def test_shift_backspace_sweep_stages() -> None:
    buf, cur = "one two three", 13
    buf, cur = _chat_shift_backspace_sweep(buf, cur, 0)
    assert buf == "one two "
    assert cur == 8
    buf, cur = _chat_shift_backspace_sweep(buf, cur, 1)
    assert buf == ""
    assert cur == 0