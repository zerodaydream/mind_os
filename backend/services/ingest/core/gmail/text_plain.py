"""Plain text, JSON, XML, and HTML attachment bodies."""

from __future__ import annotations

import re
from pathlib import Path


def strip_html_markup(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_text_plain(raw: bytes, *, mime: str = "", filename: str = "") -> str:
    text = raw.decode("utf-8", errors="replace")
    ext = Path(filename or "").suffix.lower()
    if mime.startswith("text/html") or mime == "application/xhtml+xml" or ext in {".html", ".htm"}:
        return strip_html_markup(text)
    return text