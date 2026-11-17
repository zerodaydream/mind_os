from cli.md_render import prepare_chat_line, prepare_chat_text

_DRIVE = "https://drive.google.com/file/d/13-OjKILZ-HHr5Xvuj6AbI4LWfcRLHe7/view"


def test_prepare_chat_line_label_then_full_url() -> None:
    out = prepare_chat_line(f"1. {_DRIVE}")
    lines = out.splitlines()
    assert lines[0] == "1. Google Drive"
    assert lines[1].strip() == _DRIVE


def test_prepare_chat_line_markdown_link_keeps_label() -> None:
    out = prepare_chat_line(f"[Chin Prop pdf]({_DRIVE})")
    assert out.splitlines()[0] == "Chin Prop pdf"
    assert _DRIVE in out


def test_prepare_chat_line_list_markdown_link() -> None:
    out = prepare_chat_line(f"- [invite.ics]({_DRIVE})")
    lines = out.splitlines()
    assert lines[0].endswith("invite.ics")
    assert _DRIVE in lines[1]


def test_prepare_chat_text_dedupes_bare_url_line() -> None:
    url = "https://tinyurl.com/abc"
    text = f"1. Nov 16\n{url}\n{url}"
    out = prepare_chat_text(text)
    assert out.count("tinyurl.com") == 1