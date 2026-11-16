"""DOCX attachment text."""

from __future__ import annotations

from io import BytesIO


def extract_docx(raw: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(raw))
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append("\t".join(cells))
    return "\n".join(parts)