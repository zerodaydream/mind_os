"""Chat transcript wrap width vs terminal columns."""

from cli.term import (
    _CHAT_INPUT_PREFIX,
    _chat_break_long_words,
    _chat_nav_hint,
    _chat_role_prefix,
    _chat_wrap_text,
    _chat_wrap_width,
)


def test_wrap_width_accounts_for_role_prefix():
    inner = 60
    prefix = _chat_role_prefix("mindos")
    w = _chat_wrap_width(inner, prefix)
    for chunk in _chat_wrap_text("word " * 40, w):
        assert len(prefix) + len(chunk) <= inner


def test_compact_prefix_frees_columns():
    wide = _chat_role_prefix("mindos", wide=True)
    narrow = _chat_role_prefix("mindos", wide=False)
    assert len(narrow) < len(wide)


def test_paths_break_across_lines():
    path = "/Users/x/Downloads/some_very_long_file_name.m4v"
    chunks = _chat_wrap_text(f"see {path}", 30)
    assert len(chunks) > 1
    assert _chat_break_long_words(path, 20)


def test_nav_hint_tiers():
    assert len(_chat_nav_hint(120)) > len(_chat_nav_hint(40))
    assert len(_chat_nav_hint(20)) <= 20


def test_input_prefix_not_wide_padded():
    wide = _chat_role_prefix("you", wide=True)
    assert _CHAT_INPUT_PREFIX == _chat_role_prefix("you", wide=False)
    assert len(_CHAT_INPUT_PREFIX) < len(wide)