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


def strip_override(message: str) -> tuple[Intent | None, str]:
    raw = message.strip()
    lower = raw.lower()
    for prefix, intent in _OVERRIDES.items():
        if lower.startswith(prefix + " ") or lower == prefix:
            rest = raw[len(prefix) :].strip()
            return intent, rest or raw  # type: ignore[return-value]
    return None, raw


def classify(message: str) -> tuple[Intent, str]:
    override, body = strip_override(message)
    if override is not None:
        return override, body
    lower = body.lower()
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
