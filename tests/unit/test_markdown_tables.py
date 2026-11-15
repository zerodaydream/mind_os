from backend.services.agents.core.render.markdown_tables import (
    answer_has_empty_table_shell,
    gmail_rows_markdown_table,
)


def test_empty_table_shell_detected() -> None:
    text = "| Subject | Date |\n| --- | --- |\n"
    assert answer_has_empty_table_shell(text) is True


def test_gmail_rows_table_has_data() -> None:
    md = gmail_rows_markdown_table(
        [
            {
                "title": "Re: Chin Prop",
                "direction": "sent",
                "mail_at": "2026-11-13T10:00:00+00:00",
                "to_addr": "kpriya1004@gmail.com",
                "from_addr": "me@example.com",
            }
        ]
    )
    assert "Chin Prop" in md
    assert "Nov 2026" in md
    assert answer_has_empty_table_shell(md) is False