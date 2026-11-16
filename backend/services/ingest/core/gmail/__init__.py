"""Gmail attachment policy, MIME listing, and plain-text extraction."""

from backend.services.ingest.core.gmail.errors import AttachmentExtractError
from backend.services.ingest.core.gmail.extract import extract_attachment_text
from backend.services.ingest.core.gmail.pdf import page_needs_ocr as _page_needs_ocr
from backend.services.ingest.core.gmail.pdf import pdf_image_count as _pdf_image_count
from backend.services.ingest.core.gmail.policy import (
    ATTACHMENT_MAX_BYTES,
    GMAIL_ATTACHMENT_MAX_BYTES,
    attachment_drive_name,
    attachment_drive_view_url,
    attachment_ingest_body_part,
    attachment_is_extractable,
    attachment_kind,
    attachment_skip_reason,
    gmail_format_extras_block,
    gmail_list_attachment_parts,
)

__all__ = [
    "ATTACHMENT_MAX_BYTES",
    "AttachmentExtractError",
    "GMAIL_ATTACHMENT_MAX_BYTES",
    "_page_needs_ocr",
    "_pdf_image_count",
    "attachment_drive_name",
    "attachment_drive_view_url",
    "attachment_ingest_body_part",
    "attachment_is_extractable",
    "attachment_kind",
    "attachment_skip_reason",
    "extract_attachment_text",
    "gmail_format_extras_block",
    "gmail_list_attachment_parts",
]