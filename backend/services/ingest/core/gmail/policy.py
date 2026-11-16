"""Gmail attachment policy, MIME listing, and ingest metadata."""

from __future__ import annotations

import re
from pathlib import Path

# Gmail caps attachments at 25 MiB per message (Workspace may differ).
ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024
GMAIL_ATTACHMENT_MAX_BYTES = ATTACHMENT_MAX_BYTES

_CSV_MIMES = frozenset({"text/csv", "application/csv"})
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_LEGACY_EXCEL_MIME = "application/vnd.ms-excel"
_LEGACY_WORD_MIME = "application/msword"
_IMAGE_EXTS = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
)
_IMAGE_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/heic",
        "image/heif",
        "image/heic-sequence",
        "image/heif-sequence",
    }
)


def attachment_kind(mime: str, filename: str) -> str | None:
    mime = (mime or "").split(";", 1)[0].strip().lower()
    ext = Path(filename or "").suffix.lower()
    if mime == "application/pdf" or ext == ".pdf":
        return "pdf"
    if mime == _DOCX_MIME or ext == ".docx":
        return "docx"
    if mime in _CSV_MIMES or ext == ".csv":
        return "csv"
    if mime == _XLSX_MIME or ext in {".xlsx", ".xlsm"}:
        return "xlsx"
    if mime == _LEGACY_EXCEL_MIME or ext == ".xls":
        if ext == ".csv":
            return "csv"
        return "xls"
    if mime == _LEGACY_WORD_MIME or ext == ".doc":
        return "doc"
    if mime.startswith("text/") or mime == "application/xhtml+xml":
        return "text"
    if mime in ("application/json", "application/xml", "application/x-yaml", "application/yaml") or ext in {
        ".yml",
        ".yaml",
        ".html",
        ".htm",
        ".xml",
        ".json",
        ".md",
        ".txt",
    }:
        return "text"
    if mime in _IMAGE_MIMES or ext in _IMAGE_EXTS:
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime in ("application/zip", "application/x-zip-compressed") or ext in {".zip", ".zipx"}:
        return "zip"
    return None


def attachment_is_extractable(mime: str, filename: str) -> bool:
    kind = attachment_kind(mime, filename)
    return kind is not None and kind not in {"doc", "zip"}


def attachment_skip_reason(mime: str, size: int, filename: str = "") -> str | None:
    if size > ATTACHMENT_MAX_BYTES:
        return "too_large"
    kind = attachment_kind(mime, filename)
    if kind == "doc":
        return "legacy_doc"
    if kind == "zip":
        return "zip_skipped"  # shortcut: recursive unpack later
    if attachment_is_extractable(mime, filename):
        return None
    return "binary"


def gmail_list_attachment_parts(payload: dict) -> list[dict]:
    specs: list[dict] = []

    def walk(node: dict) -> None:
        body = node.get("body") or {}
        att_id = body.get("attachmentId")
        if att_id:
            mime = node.get("mimeType") or "application/octet-stream"
            tail = mime.split("/")[-1] if "/" in mime else "bin"
            filename = (node.get("filename") or "").strip() or f"attachment.{tail}"
            specs.append(
                {
                    "filename": filename,
                    "mime_type": mime,
                    "attachment_id": att_id,
                    "size": int(body.get("size") or 0),
                }
            )
        for part in node.get("parts") or []:
            walk(part)

    if payload:
        walk(payload)
    return specs


def attachment_drive_name(msg_id: str, filename: str) -> str:
    safe_name = re.sub(r"[^\w.\-]+", "_", filename or "attachment")[:180]
    return f"mindos-gmail-{msg_id[:16]}-{safe_name}"


def attachment_drive_view_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


def attachment_ingest_body_part(
    raw: bytes, *, mime: str, filename: str
) -> tuple[str | None, str | None]:
    """Return (markdown body section, failure reason)."""
    from backend.services.ingest.core.gmail.errors import AttachmentExtractError
    from backend.services.ingest.core.gmail.extract import extract_attachment_text

    if len(raw) > ATTACHMENT_MAX_BYTES:
        return None, "too_large"
    try:
        text = extract_attachment_text(raw, mime=mime, filename=filename)
    except AttachmentExtractError as exc:
        return None, exc.reason
    return f"### Attachment: {filename}\n{text}", None


def gmail_format_extras_block(
    links: list[str],
    attachments: list[dict],
    attachment_bodies: list[str],
) -> str:
    if not links and not attachments:
        return ""
    lines = ["---"]
    if links:
        lines.append("Links:")
        lines.extend(f"- {u}" for u in links)
    if attachments:
        lines.append("Attachments:")
        for rec in attachments:
            if rec.get("ingested"):
                line = f"- {rec['filename']} (ingested)"
            else:
                line = f"- {rec['filename']} (not ingested: {rec.get('reason', 'unknown')})"
            if url := rec.get("drive_url"):
                line = f"{line}: {url}"
            lines.append(line)
    lines.extend(attachment_bodies)
    return "\n".join(lines)