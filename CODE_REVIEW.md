# Code review — Gmail attachment text extractors

**Scope:** `attachment_text.py`, `sync.py` (extras path), `pyproject.toml`, unit tests.  
**Mode:** Small diff (fast path). **Tests:** 12 passed (`test_attachment_text`, `test_gmail_extras`).

## Summary

| Severity | Count | Notes |
|----------|-------|-------|
| Critical | 0 | — |
| Major | 2 | Known format limits (see below) |
| Minor | 2 | Follow-ups, not blockers |

## Findings

### Major — image-only / scanned PDFs (`empty_extract`)

**Evidence:** `attachment_text.py` — `empty_extract` when `pypdf` returns no text; vault file still saved, `ingested: false` in metadata.  
**Impact:** Common for scanned resumes; retrieval will not see content until OCR (not implemented).  
**Mitigation (future):** OCR pass or mark `metadata.attachments[].needs_ocr`.

### Major — legacy `.doc` still skipped

**Evidence:** `attachment_is_extractable` excludes `kind == "doc"`.  
**Impact:** Old Word attachments remain `binary` at pre-check.  
**Mitigation:** LibreOffice/pandoc path (already elsewhere in MCP convert tools) if needed.

### Minor — 1M char cap on extracted text

**Evidence:** `_EXTRACTED_TEXT_MAX_CHARS` in `attachment_text.py`.  
**Impact:** Huge CSV/XLSX lose tail; hybrid keyword on full mail body may still miss tail-only rows.  
**Acceptable:** Documented shortcut until per-attachment artifacts.

### Minor — attachments still merged into one email artifact

**Evidence:** `gmail_ingest_extras` → single `ingest_manual_note` body.  
**Impact:** Large attachments dilute chunk boundaries vs separate artifacts (retrieval structure).  
**Not a bug:** Matches prior design; user accepted heavier sync.

## Security

- Parsers run on user’s own Gmail bytes, size-capped at 25 MiB; no new network trust boundaries.
- No secrets in logs; extract errors use typed reasons only.

## Verdict

**PASS for merge** — correct wiring, tests, graceful failure. Not “perfect” for all attachment types (OCR, `.doc`, PPTX, RTF intentionally out of scope).