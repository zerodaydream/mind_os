from backend.services.agents.core.routing.turn_prep import TurnPrep
from backend.services.agents.core.turn.turn_retrieval import (
    _gmail_attachment_url_lines,
    _mail_followup_topic,
    _pronoun_recipient,
    format_context_text,
    turn_search_query,
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


def test_pivot_message_does_not_prepend_old_topic() -> None:
    hist = [
        {"role": "user", "content": "tell me about Chin Prop mail"},
        {"role": "assistant", "content": "summary"},
    ]
    msg = "actually show me calendar events for next week instead please"
    prep = TurnPrep(
        in_scope=True,
        intent="plan",
        task=msg,
        search_query=msg,
        prefetch_mail=False,
        prefetch_commitments=False,
        prefetch_memories=True,
    )
    assert turn_search_query(msg, hist, prep) == msg
    assert "Chin Prop" not in turn_search_query(msg, hist, prep)


def test_vague_followup_prepends_topic() -> None:
    hist = [
        {"role": "user", "content": "tell me about Chin Prop mail"},
        {"role": "assistant", "content": "summary"},
    ]
    msg = "tell me more"
    prep = TurnPrep(
        in_scope=True,
        intent="plan",
        task=msg,
        search_query=msg,
        prefetch_mail=True,
        prefetch_commitments=False,
        prefetch_memories=False,
    )
    q = turn_search_query(msg, hist, prep)
    assert "Chin Prop" in q


def test_context_clip_keeps_newest_within_budget() -> None:
    rows = [
        {
            "title": f"Subject {i}",
            "from_addr": "a@b.co",
            "to_addr": "",
            "direction": "received",
            "mail_at": "",
            "preview": "body " + ("x" * 400),
            "metadata": {},
        }
        for i in range(20)
    ]
    text = format_context_text(
        results=[],
        commitments=[],
        memories=[],
        gmail_ctx=rows,
        max_chars=800,
    )
    assert len(text) <= 800
    assert "Subject 0" in text