from cli.md_render import prepare_chat_line, prepare_chat_text


def test_prepare_chat_line_strips_duplicate_markdown_url() -> None:
    url = "https://www.google.com/calendar/event?eid=abc"
    line = f"[{url}]({url})"
    out = prepare_chat_line(line)
    assert out.count("google.com") == 1
    assert "[" not in out


def test_prepare_chat_text_dedupes_bare_url_line() -> None:
    url = "https://tinyurl.com/abc"
    text = f"1. Nov 16\n{url}\n{url}"
    out = prepare_chat_text(text)
    assert out.count(url) == 1