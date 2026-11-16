"""Raster image attachment text (OCR)."""

from __future__ import annotations

from backend.services.ingest.core.gmail.errors import AttachmentExtractError
from backend.services.ingest.core.gmail.ocr import ocr_image_bytes, rapid_ocr_engine, rapid_ocr_lines


def extract_image(raw: bytes, *, mime: str = "", filename: str = "") -> str:
    ocr_input = ocr_image_bytes(raw, mime=mime, filename=filename)
    result, _ = rapid_ocr_engine()(ocr_input)
    text = rapid_ocr_lines(result)
    if text.strip():
        return text
    raise AttachmentExtractError("empty_extract")