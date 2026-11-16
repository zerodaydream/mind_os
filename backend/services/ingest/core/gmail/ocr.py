"""Shared OCR helpers for image and PDF attachment text."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

_rapid_ocr: Any = None


def rapid_ocr_engine():
    global _rapid_ocr
    if _rapid_ocr is None:
        from rapidocr_onnxruntime import RapidOCR

        _rapid_ocr = RapidOCR()
    return _rapid_ocr


def rapid_ocr_lines(result: object) -> str:
    if not result:
        return ""
    lines: list[str] = []
    for row in result:
        if isinstance(row, (list, tuple)) and len(row) > 1:
            text = row[1]
            if isinstance(text, str) and text.strip():
                lines.append(text.strip())
    return "\n".join(lines)


def ocr_image_bytes(raw: bytes, *, mime: str, filename: str) -> bytes:
    mime = (mime or "").split(";", 1)[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    if ext not in {".heic", ".heif"} and mime not in ("image/heic", "image/heif"):
        return raw
    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    buf = BytesIO()
    Image.open(BytesIO(raw)).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()