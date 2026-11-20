"""Deep-agent subagents: researcher, calendar-operator, file-scout (isolated context)."""

from __future__ import annotations

from typing import Any

from backend.services.integrations.mcp.filesystem_tools import (
    build_filesystem_tools,
    filesystem_enabled,
)


_RESEARCHER_PROMPT = """You are the MindOS researcher subagent (read-only).
Isolate multi-hop search from the lead agent.
Use search / mindos_search / mindos_mail_lookup / read_url only.
Return at most ~400 tokens: bullet findings with Evidence [E#] or artifact ids.
Never invent filenames or URLs. Never call sync, calendar writes, or filesystem writes.
"""

_CALENDAR_PROMPT = """You are the MindOS calendar-operator subagent.
Own server-side time resolution and calendar tools only (calendar_query / calendar_change
or mindos_calendar_*).
Return a short event summary and link. For deletes/bulk changes, require confirmation.
Never search mail or local disk.
"""

_FILE_SCOUT_PROMPT = """You are the MindOS file-scout subagent (read-only disk hunt).
Use when the index misses and a local file hunt is needed.

Protocol:
1. Prefer files_find / mindos_fs_locate / mindos_fs_glob with an explicit root
   (~/Downloads, ~/Desktop, ~/Documents) — never scan / alone.
2. Use files_read / mindos_fs_read only to confirm a candidate (range-limited).
3. Never write, rename, or delete.
4. Return at most 8 candidates as:
   - absolute_path — one-line reason (mtime/size/match)
Stop after a confident hit. Cap total tool output; be concise.
"""


def build_subagents(
    *,
    personal_tools: list[Any],
    on_partial=None,
    user_task: str = "",
) -> list[dict[str, Any]]:
    """Declarative SubAgent specs for create_deep_agent (isolated mode)."""
    by_name = {
        (getattr(t, "name", None) or getattr(t, "__name__", "") or ""): t
        for t in personal_tools
    }

    def _pick(*names: str) -> list[Any]:
        return [by_name[n] for n in names if n in by_name]

    research_tools = _pick(
        "search",
        "mindos_search",
        "mindos_mail_lookup",
        "read_url",
        "mindos_summarize_url",
        "read_artifact",
        "commitments",
        "mindos_commitments",
        "memory_search",
    )
    calendar_tools = _pick(
        "calendar_query",
        "calendar_change",
        "mindos_calendar_list_upcoming",
        "mindos_calendar_create_event",
        "mindos_calendar_update_event",
        "mindos_calendar_delete_event",
        "mindos_calendar_list_calendars",
        "mindos_calendar_free_busy",
    )

    subagents: list[dict[str, Any]] = [
        {
            "name": "researcher",
            "description": (
                "Multi-hop mail/document research. Use when comparing threads or "
                "gathering evidence would flood the lead context (~3k+ tokens). "
                "Returns ≤400 tokens of findings with eids."
            ),
            "system_prompt": _RESEARCHER_PROMPT,
            "tools": research_tools,
        },
        {
            "name": "calendar-operator",
            "description": (
                "Calendar reads and writes. Delegate any create/update/delete and "
                "timezone resolution here. Interrupt before destructive changes."
            ),
            "system_prompt": _CALENDAR_PROMPT,
            "tools": calendar_tools,
            "interrupt_on": {
                "calendar_change": True,
                "mindos_calendar_delete_event": True,
                "mindos_calendar_create_event": True,
            },
        },
    ]

    if filesystem_enabled():
        scout_tools = build_filesystem_tools(
            on_partial=on_partial, user_task=user_task, scout_only=True
        )
        if scout_tools:
            subagents.append(
                {
                    "name": "file-scout",
                    "description": (
                        "Local disk hunt when the index misses. Use for finding files under "
                        "configured roots (Downloads/Desktop/Documents). Read-only. "
                        "Returns candidate absolute paths with one-line reasons. "
                        "Delegate via task(name='file-scout', ...) when search finds nothing "
                        "and the user likely means a local path."
                    ),
                    "system_prompt": _FILE_SCOUT_PROMPT,
                    "tools": scout_tools,
                }
            )

    return [s for s in subagents if s.get("tools")]
