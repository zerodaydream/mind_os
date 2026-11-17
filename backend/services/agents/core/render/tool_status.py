"""User-facing status lines while agent tools run."""

from __future__ import annotations

import re

_TOOL_TRACE_SUFFIX = re.compile(r"^(.+)_[a-f0-9]{10}$")

_TOOL_STATUS: dict[str, str] = {
    "mindos_search": "Searching your mail and indexed documents",
    "mindos_gmail_sync": "Syncing Gmail",
    "mindos_process_outbox": "Indexing new mail and attachments",
    "mindos_commitments": "Listing open commitments",
    "mindos_summarize_url": "Summarizing document",
    "mindos_google_integration_status": "Checking Google account connection",
    "mindos_calendar_create_event": "Creating calendar event",
    "mindos_calendar_list_upcoming": "Listing upcoming events",
    "mindos_calendar_update_event": "Updating calendar event",
    "mindos_calendar_delete_event": "Deleting calendar event",
    "mindos_calendar_list_calendars": "Listing calendars",
    "mindos_calendar_free_busy": "Checking calendar availability",
    "mindos_convert_capabilities": "Checking conversion options",
    "mindos_convert_file": "Converting file",
    "mindos_convert_batch": "Converting files",
    "mindos_fs_roots": "Listing workspace folders",
    "mindos_fs_list": "Listing folder contents",
    "mindos_fs_grep": "Grepping file contents",
    "mindos_fs_glob": "Finding files by pattern",
    "mindos_fs_locate": "Searching for files",
    "mindos_fs_find": "Searching files on disk",
    "mindos_fs_read": "Reading file",
    "mindos_fs_write": "Writing file",
    "mindos_fs_rename": "Renaming file",
    "mindos_fs_delete": "Deleting file",
    "write_todos": "Planning next steps",
    "fetch_url": "Opening link",
    "search_web": "Searching the web",
}


def tool_status_line(name: str) -> str:
    """Professional in-progress label for a tool name."""
    key = (name or "").strip().lower()
    m = _TOOL_TRACE_SUFFIX.match(key)
    if m:
        key = m.group(1)
    if key in _TOOL_STATUS:
        return _TOOL_STATUS[key]
    if key.startswith("fetch_"):
        return "Fetching from the web"
    slug = key.removeprefix("mindos_").replace("_", " ").strip()
    if slug:
        return f"Running {slug}"
    return "Working on your request"