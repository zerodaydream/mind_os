from datetime import timezone

from backend.services.integrations.core.gmail_sync import (
    gmail_artifact_body,
    gmail_direction,
    gmail_header_block,
    gmail_occurred_at,
    parse_email_addresses,
)


def test_gmail_header_block_includes_to() -> None:
    block = gmail_header_block(
        {
            "From": "me@example.com",
            "To": "kpriya1004@example.com",
            "Subject": "Hello",
        }
    )
    assert "To: kpriya1004@example.com" in block


def test_parse_emails() -> None:
    assert parse_email_addresses("Krishna <kpriya1004@gmail.com>") == ["kpriya1004@gmail.com"]


def test_gmail_direction_sent() -> None:
    assert gmail_direction(["SENT", "INBOX"]) == "sent"
    assert gmail_direction(["INBOX"]) == "received"


def test_gmail_occurred_at_ms() -> None:
    dt = gmail_occurred_at({"internalDate": "1600000000000"})
    assert dt is not None and dt.tzinfo == timezone.utc


def test_gmail_artifact_body_preserves_body() -> None:
    body = gmail_artifact_body(
        {"From": "a", "To": "b", "Subject": "S"},
        "message text",
    )
    assert "To: b" in body
    assert "message text" in body