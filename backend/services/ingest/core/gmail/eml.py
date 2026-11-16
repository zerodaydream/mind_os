"""`.eml` / message/rfc822 attachment bodies (stdlib)."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser

from backend.services.ingest.core.gmail.text_plain import strip_html_markup


def extract_eml(raw: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    lines: list[str] = []
    for header in ("Subject", "From", "To", "Date"):
        val = (msg.get(header) or "").strip()
        if val:
            lines.append(f"{header}: {val}")
    body = msg.get_body(preferencelist=("plain",))
    if body is None:
        body = msg.get_body(preferencelist=("html",))
    if body:
        content = body.get_content()
        if isinstance(content, str):
            if (body.get_content_type() or "").startswith("text/html"):
                content = strip_html_markup(content)
            lines.append(content)
    return "\n\n".join(part for part in lines if part.strip())