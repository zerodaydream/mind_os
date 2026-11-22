"""Taint tracking: marks a run when untrusted content has entered context (ARCHITECTURE §14.4)."""

from __future__ import annotations

from enum import StrEnum


class TaintSource(StrEnum):
    EXTERNAL_EMAIL = "external_email"
    EXTERNAL_DOC = "external_doc"
    WEB = "web"
    MCP = "mcp"
    TOOL_OUTPUT = "tool_output"
