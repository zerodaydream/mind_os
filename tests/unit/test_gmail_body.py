import base64

from backend.services.integrations.core.gmail_sync import gmail_message_text


def test_plain_part_decoded() -> None:
    raw = base64.urlsafe_b64encode(b"Hello inbox").decode().rstrip("=")
    payload = {"mimeType": "text/plain", "body": {"data": raw}}
    assert gmail_message_text(payload) == "Hello inbox"
