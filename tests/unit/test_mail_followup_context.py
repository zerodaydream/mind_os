from backend.services.agents.core.turn.turn_retrieval import (
    _gmail_attachment_url_lines,
    _mail_followup_topic,
    _pronoun_recipient,
    format_context_text,
)


def test_followup_topic_from_prior_user_turn() -> None:
    hist = [
        {"role": "user", "content": "tell me about Chin Prop mail"},
        {"role": "assistant", "content": "summary"},
    ]
    assert _mail_followup_topic("give me their gdrive url links", hist) == "tell me about Chin Prop mail"


def test_drive_lines_in_context_block() -> None:
    meta = {
        "attachments": [
            {"filename": "a.pdf", "drive_url": "https://drive.google.com/file/d/ABC/view"},
        ],
    }
    text = format_context_text(
        results=[],
        commitments=[],
        memories=[],
        gmail_ctx=[
            {
                "title": "Chin Prop",
                "from_addr": "x",
                "to_addr": "",
                "direction": "received",
                "mail_at": "",
                "metadata": meta,
            }
        ],
    )
    assert "Drive · a.pdf" in text
    assert "ABC" in text


def test_gmail_attachment_url_lines_from_file_id() -> None:
    lines = _gmail_attachment_url_lines(
        {"attachments": [{"filename": "x.pdf", "drive_file_id": "XYZ"}]}
    )
    assert lines[0].endswith("/file/d/XYZ/view")


def test_pronoun_recipient_from_prior_sent_to() -> None:
    hist = [
        {"role": "user", "content": "urls of project x i sent to alex rivera"},
    ]
    assert _pronoun_recipient("yaml i sent to her as well", hist) == "alex rivera"


def test_attachment_line_without_drive_still_lists_name() -> None:
    lines = _gmail_attachment_url_lines(
        {"attachments": [{"filename": "real-config.yaml", "reason": "empty_extract"}]}
    )
    assert lines == ["  attachment · real-config.yaml"]