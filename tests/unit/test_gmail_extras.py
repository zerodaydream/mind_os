import base64

from backend.services.ingest.core.gmail import (
    GMAIL_ATTACHMENT_MAX_BYTES,
    attachment_drive_view_url,
    attachment_skip_reason,
    gmail_format_extras_block,
    gmail_list_attachment_parts,
)
from backend.services.integrations.core.gmail.sync import (
    extract_urls_from_html,
    extract_urls_from_text,
    gmail_collect_links,
)


def test_extract_urls_from_text() -> None:
    assert extract_urls_from_text("see https://example.com/a and http://x.test") == [
        "https://example.com/a",
        "http://x.test",
    ]


def test_extract_urls_from_html_href() -> None:
    html = '<a href="https://foo.bar/path">link</a>'
    assert "https://foo.bar/path" in extract_urls_from_html(html)


def test_gmail_collect_links_from_plain_part() -> None:
    raw = base64.urlsafe_b64encode(b"Go to https://jobs.example/role").decode().rstrip("=")
    payload = {"mimeType": "text/plain", "body": {"data": raw}}
    assert gmail_collect_links(payload) == ["https://jobs.example/role"]


def test_gmail_list_attachment_parts_nested() -> None:
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"size": 3}},
            {
                "mimeType": "application/pdf",
                "filename": "resume.pdf",
                "body": {"attachmentId": "ANGjdJ8", "size": 1200},
            },
        ],
    }
    parts = gmail_list_attachment_parts(payload)
    assert len(parts) == 1
    assert parts[0]["filename"] == "resume.pdf"
    assert parts[0]["attachment_id"] == "ANGjdJ8"


def test_attachment_skip_reason() -> None:
    assert attachment_skip_reason("application/pdf", 100, "resume.pdf") is None
    assert attachment_skip_reason("application/pdf", 100) is None
    assert attachment_skip_reason("text/plain", 100) is None
    assert attachment_skip_reason("text/plain", GMAIL_ATTACHMENT_MAX_BYTES + 1) == "too_large"


def test_attachment_drive_view_url() -> None:
    assert attachment_drive_view_url("abc123") == "https://drive.google.com/file/d/abc123/view"


def test_gmail_format_extras_block_status_lines() -> None:
    block = gmail_format_extras_block(
        ["https://a.com"],
        [
            {
                "filename": "a.txt",
                "ingested": True,
                "drive_url": "https://drive.google.com/file/d/x/view",
            },
            {"filename": "b.bin", "ingested": False, "reason": "binary"},
        ],
        [],
    )
    assert "Links:" in block
    assert "https://a.com" in block
    assert "a.txt (ingested): https://drive.google.com/file/d/x/view" in block
    assert "b.bin (not ingested: binary)" in block