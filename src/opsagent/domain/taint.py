"""Taint tracking: marks a run when untrusted content has entered context (ARCHITECTURE §14.4)."""

from __future__ import annotations

from enum import StrEnum


class TaintSource(StrEnum):
    EXTERNAL_EMAIL = "external_email"
    EXTERNAL_DOC = "external_doc"
    WEB = "web"
    MCP = "mcp"
    TOOL_OUTPUT = "tool_output"


class TaintLedger:
    """Which runs have read untrusted content, and from where (ARCHITECTURE section 14.4).

    A run is tainted from the moment untrusted content enters its context. Only code adds taint:
    the engine marks a run after a tool whose output is untrusted returns. The model cannot clear
    it. A new run (a new user message) starts clean.
    """

    def __init__(self) -> None:
        self._by_run: dict[str, set[TaintSource]] = {}

    def mark(self, run_id: str | None, source: TaintSource) -> None:
        self._by_run.setdefault(run_id or "", set()).add(source)

    def sources(self, run_id: str | None) -> frozenset[TaintSource]:
        return frozenset(self._by_run.get(run_id or "", ()))

    def is_tainted(self, run_id: str | None) -> bool:
        return bool(self._by_run.get(run_id or ""))

    def forget(self, run_id: str | None) -> None:
        """Drop a run's taint. A resumed run is re-marked from its approval record."""
        self._by_run.pop(run_id or "", None)
