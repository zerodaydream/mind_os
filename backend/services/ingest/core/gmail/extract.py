"""Dispatch attachment bytes to MIME-specific extractors."""

from __future__ import annotations

import re

from backend.services.ingest.core.gmail import csv as csv_mod
from backend.services.ingest.core.gmail import docx as docx_mod
from backend.services.ingest.core.gmail import image as image_mod
from backend.services.ingest.core.gmail import media as media_mod
from backend.services.ingest.core.gmail import pdf as pdf_mod
from backend.services.ingest.core.gmail import spreadsheet as sheet_mod
from backend.services.ingest.core.gmail import text_plain as text_mod
from backend.services.ingest.core.gmail.errors import AttachmentExtractError
from backend.services.ingest.core.gmail.policy import attachment_kind

# shortcut: single-string cap before chunker, upgrade when attachments are separate artifacts
_EXTRACTED_TEXT_MAX_CHARS = 1_000_000


def _cap(text: str) -> str:
    if len(text) <= _EXTRACTED_TEXT_MAX_CHARS:
        return text
    return text[:_EXTRACTED_TEXT_MAX_CHARS].rstrip() + "\n…[truncated]"


def extract_attachment_text(raw: bytes, *, mime: str, filename: str) -> str:
    kind = attachment_kind(mime, filename)
    if kind is None:
        raise AttachmentExtractError("unsupported_type")
    if kind == "doc":
        raise AttachmentExtractError("legacy_doc")
    try:
        if kind == "text":
            text = text_mod.extract_text_plain(raw)
        elif kind == "pdf":
            text = pdf_mod.extract_pdf(raw)
        elif kind == "docx":
            text = docx_mod.extract_docx(raw)
        elif kind == "csv":
            text = csv_mod.extract_csv(raw)
        elif kind == "xlsx":
            text = sheet_mod.extract_xlsx(raw)
        elif kind == "xls":
            text = sheet_mod.extract_xls(raw)
        elif kind == "image":
            text = image_mod.extract_image(raw, mime=mime, filename=filename)
        elif kind in ("audio", "video"):
            text = media_mod.extract_media_stub(mime=mime, filename=filename, size=len(raw))
        else:
            raise AttachmentExtractError("unsupported_type")
    except AttachmentExtractError:
        raise
    except ImportError as exc:
        mod = getattr(exc, "name", None) or "unknown"
        raise AttachmentExtractError(f"missing_dependency:{mod}") from exc
    except Exception as exc:
        raise AttachmentExtractError(f"extract_failed:{type(exc).__name__}") from exc

    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    if not text:
        raise AttachmentExtractError("empty_extract")
    return _cap(text)