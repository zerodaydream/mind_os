from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.retrieval.core.mail_lookup import (
    attachment_lines_from_metadata,
    format_mail_lookup_text,
    load_mail_bodies_batch,
)


def test_format_lookup_includes_body_and_drive() -> None:
    text = format_mail_lookup_text(
        [
            {
                "title": "GPU credits",
                "mail_at": "Mon",
                "to_addr": "you@co",
                "from_addr": "them@co",
                "direction": "received",
                "body": "Line one\n$500 sponsorship",
                "metadata": {
                    "attachments": [
                        {"filename": "deck.pdf", "drive_url": "https://drive.google.com/file/d/ABC/view"},
                    ]
                },
            }
        ]
    )
    assert "$500" in text
    assert "Drive · deck.pdf" in text
    assert "ABC" in text


def test_attachment_lines_from_file_id() -> None:
    lines = attachment_lines_from_metadata(
        {"attachments": [{"filename": "x.pdf", "drive_file_id": "XYZ"}]}
    )
    assert lines[0].endswith("/file/d/XYZ/view")


@pytest.mark.asyncio
async def test_load_mail_bodies_batch_includes_later_chunks() -> None:
    aid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row = MagicMock()
    row.all.return_value = [(aid, "intro\n\n$500 only in chunk two")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=row)
    bodies = await load_mail_bodies_batch(session, [aid])
    assert "chunk two" in bodies[aid]
    assert "$500" in bodies[aid]