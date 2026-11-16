import csv
from io import BytesIO, StringIO

import pytest

from backend.services.ingest.core.gmail import (
    AttachmentExtractError,
    _page_needs_ocr,
    _pdf_image_count,
    attachment_is_extractable,
    attachment_kind,
    attachment_skip_reason,
    extract_attachment_text,
)

pytest.importorskip("pymupdf")
pytest.importorskip("pymupdf4llm")
pytest.importorskip("rapidocr_onnxruntime")
pytest.importorskip("docx")
pytest.importorskip("openpyxl")


def test_attachment_kind_html_by_extension() -> None:
    assert attachment_kind("application/octet-stream", "page.html") == "text"
    assert attachment_is_extractable("application/octet-stream", "page.html") is True


def test_extract_html_attachment_strips_tags() -> None:
    raw = b"<html><body><p>Hello</p></body></html>"
    text = extract_attachment_text(raw, mime="application/octet-stream", filename="x.html")
    assert "Hello" in text
    assert "<p>" not in text


def test_attachment_kind_csv_by_extension() -> None:
    assert attachment_kind("application/octet-stream", "data.CSV") == "csv"
    assert attachment_kind("application/x-yaml", "docker-compose.yml") == "text"
    assert attachment_kind("application/octet-stream", "stack.yaml") == "text"
    assert attachment_is_extractable("application/pdf", "x.pdf") is True
    assert attachment_kind("image/jpeg", "scan.jpg") == "image"
    assert attachment_kind("image/heic", "IMG_0001.HEIC") == "image"
    assert attachment_kind("image/heif", "photo.heif") == "image"
    assert attachment_kind("", "photo.JPG") == "image"
    assert attachment_kind("audio/mpeg", "note.mp3") == "audio"
    assert attachment_kind("video/mp4", "clip.mp4") == "video"
    assert attachment_skip_reason("video/mp4", 100, "a.mp4") is None


def test_attachment_skip_reason_pdf() -> None:
    assert attachment_skip_reason("application/pdf", 100, "a.pdf") is None
    assert attachment_skip_reason("application/pdf", 100) is None
    assert attachment_kind("application/zip", "bundle.zip") == "zip"
    assert attachment_skip_reason("application/zip", 100, "a.zip") == "zip_skipped"


def test_extract_csv() -> None:
    buf = StringIO()
    csv.writer(buf).writerow(["name", "score"])
    csv.writer(buf).writerow(["ann", "9"])
    text = extract_attachment_text(
        buf.getvalue().encode(), mime="text/csv", filename="t.csv"
    )
    assert "name" in text and "ann" in text


def test_extract_docx_minimal() -> None:
    from docx import Document

    bio = BytesIO()
    doc = Document()
    doc.add_paragraph("Offer letter body")
    doc.save(bio)
    text = extract_attachment_text(bio.getvalue(), mime="", filename="offer.docx")
    assert "Offer letter body" in text


def test_extract_xlsx_minimal() -> None:
    from openpyxl import Workbook

    bio = BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["role", "company"])
    ws.append(["eng", "Acme"])
    wb.save(bio)
    text = extract_attachment_text(
        bio.getvalue(),
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="jobs.xlsx",
    )
    assert "Acme" in text and "Sheet1" in text


def test_pdf_image_count_on_text_only_pdf() -> None:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello")
    assert _pdf_image_count(doc) == 0
    assert _page_needs_ocr(page, "hello world") is False
    doc.close()


def test_extract_pdf_text() -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Offer details here")
    bio = BytesIO()
    doc.save(bio)
    doc.close()
    text = extract_attachment_text(bio.getvalue(), mime="application/pdf", filename="resume.pdf")
    assert "Offer details" in text


def test_extract_media_stub() -> None:
    text = extract_attachment_text(b"\x00", mime="audio/mpeg", filename="voice.mp3")
    assert "voice.mp3" in text and "Media attachment" in text


def test_extract_pdf_blank_page_raises_empty() -> None:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    bio = BytesIO()
    doc.save(bio)
    doc.close()
    with pytest.raises(AttachmentExtractError, match="empty_extract"):
        extract_attachment_text(bio.getvalue(), mime="application/pdf", filename="blank.pdf")