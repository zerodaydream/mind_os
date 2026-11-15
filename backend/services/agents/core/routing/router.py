"""Classify chat turns: converse | plan | execute."""

from __future__ import annotations

import re
from typing import Literal

Intent = Literal["converse", "plan", "execute"]

_OVERRIDES = {
    "/chat": "converse",
    "/plan": "plan",
    "/do": "execute",
    "/exec": "execute",
}

_PLAN = (
    "plan",
    "break down",
    "roadmap",
    "steps to",
    "how should i",
    "strategy",
    "outline",
    "todo list",
    "make a plan",
)

_EXECUTE = (
    "create file",
    "edit file",
    "write a script",
    "implement",
    "apply patch",
    "refactor",
    "fix the bug",
    "run the",
    "delete file",
    "mkdir",
    "write to ",
)

_INVESTIGATE = (
    "find me",
    "find the",
    "find all",
    "list of",
    "list all",
    "show me",
    "show all",
    "get me",
    "pull up",
    "look up",
    "search for",
    "search my",
    "read their",
    "as a table",
    "i want you to",
    "i want to",
    "please find",
    "please list",
    "please show",
)

_JOB_SIGNALS = (
    "indeed",
    "job",
    "jobs",
    "application",
    "applied",
    "role",
    "roles",
    "hiring",
    "recruiter",
    "interview",
)

_ACTION_SIGNALS = (
    "find",
    "list",
    "show",
    "search",
    "read",
    "gather",
    "table",
    "sync",
)

_HTTP_URL = re.compile(r"https?://\S+", re.I)

_URL_DOC_FOLLOWUP = (
    "sheet",
    "tab",
    "row",
    "rows",
    "column",
    "cell",
    "excel",
    "spreadsheet",
    "worksheet",
    "paycheck",
    "income",
    "expense",
    "balance",
    "this doc",
    "this sheet",
    "this link",
    "that sheet",
    "the sheet",
    "the tab",
    "from the",
    "in the",
    "on the",
    "read",
    "list",
    "show",
)


def url_from_thread(message: str, history: list[dict] | None) -> str | None:
    m = _HTTP_URL.search(message or "")
    if m:
        return m.group(0).rstrip(").,]")
    if not history:
        return None
    thread = "\n".join((entry.get("content") or "") for entry in history[-10:])
    m = _HTTP_URL.search(thread)
    if not m:
        return None
    return m.group(0).rstrip(").,]")


def needs_url_agent(message: str, history: list[dict] | None = None) -> bool:
    """Links/PDFs need the deep agent (mindos_summarize_url), not converse synth."""
    lower = message.lower()
    if _HTTP_URL.search(message):
        return any(
            w in lower
            for w in (
                "summarize",
                "summary",
                "summerize",
                "pdf",
                "read",
                "fetch",
                "this link",
                "this url",
                "this page",
                "tell me about",
                "what does",
                "what is",
                "explain",
                "what was",
                "what were",
                "give me",
            )
        )
    if not history:
        return False
    thread = "\n".join((m.get("content") or "") for m in history[-8:])
    if not _HTTP_URL.search(thread):
        return False
    if any(w in lower for w in _URL_DOC_FOLLOWUP):
        return True
    return any(
        w in lower
        for w in (
            "author",
            "researcher",
            "who wrote",
            "who worked",
            "title",
            "paper",
            "abstract",
            "summarize",
            "summary",
        )
    )


def strip_override(message: str) -> tuple[Intent | None, str]:
    raw = message.strip()
    lower = raw.lower()
    for prefix, intent in _OVERRIDES.items():
        if lower.startswith(prefix + " ") or lower == prefix:
            rest = raw[len(prefix) :].strip()
            return intent, rest or raw  # type: ignore[return-value]
    return None, raw


def _calendar_execute(lower: str) -> bool:
    if "calendar" in lower or "calender" in lower:
        return True
    if re.search(r"\b(delete|cancel|remove)\b", lower) and any(
        w in lower for w in ("calendar", "calender", "meeting", "event", "appointment")
    ):
        return True
    if re.search(r"\bschedule\b", lower) and re.search(
        r"\b(at\s+\d|:\d|pm|am|tomorrow|today|schedule\s+it)\b", lower
    ):
        return True
    has_sched = any(
        w in lower for w in ("event", "meeting", "appointment", "reminder")
    )
    if not has_sched:
        return False
    return bool(
        re.search(r"\b(create|schedule|add|book|set up|set-up)\b", lower)
    )


def classify(message: str) -> tuple[Intent, str]:
    override, body = strip_override(message)
    if override is not None:
        return override, body
    lower = body.lower()
    if needs_url_agent(body):
        return "plan", body
    if _calendar_execute(lower):
        return "execute", body
    if any(p in lower for p in _EXECUTE):
        return "execute", body
    if any(p in lower for p in _PLAN):
        return "plan", body
    if any(p in lower for p in _INVESTIGATE):
        return "plan", body
    if any(j in lower for j in _JOB_SIGNALS) and any(a in lower for a in _ACTION_SIGNALS):
        return "plan", body
    # multi-part numbered asks (e.g. "1. Find … 2. Read …")
    if re.search(r"(?m)^\s*\d+\.\s", body) and body.count("\n") >= 1:
        return "plan", body
    # long multi-step asks without keywords → plan
    if len(body) > 400 and (" then " in lower or " and then " in lower or lower.count("\n") >= 3):
        return "plan", body
    return "converse", body
