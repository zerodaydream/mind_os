"""PDF attachment text (native extract + OCR fallback)."""

from __future__ import annotations

from backend.services.ingest.core.gmail.errors import AttachmentExtractError
from backend.services.ingest.core.gmail.ocr import rapid_ocr_engine, rapid_ocr_lines

_PAGE_MIN_CHARS = 40
_PDF_OCR_DPI = 200


def pdf_image_count(doc) -> int:
    return sum(len(page.get_images()) for page in doc)


def page_needs_ocr(page, native: str) -> bool:
    if len(native) >= _PAGE_MIN_CHARS:
        return False
    return len(page.get_images()) > 0


def _pdf_text_seems_incomplete(page_count: int, text: str) -> bool:
    # shortcut: min chars/page heuristic, upgrade if resumes rank poorly
    return len(text.strip()) < max(_PAGE_MIN_CHARS, page_count * _PAGE_MIN_CHARS)


def _page_text(page) -> str:
    native = (page.get_text("text", sort=True) or "").strip()
    if not page_needs_ocr(page, native):
        return native
    try:
        pix = page.get_pixmap(dpi=_PDF_OCR_DPI, alpha=False)
        result, _ = rapid_ocr_engine()(pix.tobytes("png"))
        ocr = rapid_ocr_lines(result)
        return ocr if len(ocr) > len(native) else native
    except (ImportError, OSError, RuntimeError, ValueError):
        return native


def extract_pdf(raw: bytes) -> str:
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(stream=raw, filetype="pdf")
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise AttachmentExtractError("password_protected")
        parts = [_page_text(page) for page in doc]
        text = "\n\n".join(part for part in parts if part)
        llm_kwargs = {
            "write_images": False,
            "embed_images": False,
            "header": False,
            "footer": False,
        }
        if _pdf_text_seems_incomplete(doc.page_count, text) and pdf_image_count(doc) > 0:
            layout = pymupdf4llm.to_markdown(doc, use_ocr=False, **llm_kwargs).strip()
            if len(layout) > len(text):
                text = layout
        elif not _pdf_text_seems_incomplete(doc.page_count, text):
            layout = pymupdf4llm.to_markdown(doc, use_ocr=False, **llm_kwargs).strip()
            if len(layout) > len(text):
                text = layout
    finally:
        doc.close()
    return text