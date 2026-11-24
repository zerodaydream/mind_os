"""Slash command catalog and the shortcuts panel text. Pure data, no Textual."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SlashCommand:
    name: str
    summary: str
    ready: bool = True  # False: listed, but the owning phase has not shipped yet


CATALOG: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "Show commands and shortcuts"),
    SlashCommand("/clear", "Clear the transcript"),
    SlashCommand("/exit", "Quit"),
    SlashCommand("/think", "Pick reasoning effort, or /think [subagent] <level>"),
    SlashCommand("/status", "Show session, models, mode, and run state"),
    SlashCommand("/cost", "Show token use and cost for this session"),
    SlashCommand("/model", "Pick the model, or /model [subagent] <id>"),
    SlashCommand("/doctor", "Run `mindos doctor` in another terminal", ready=False),
    SlashCommand("/resume", "Resume a past conversation", ready=False),
    SlashCommand("/memory", "Show and edit saved memory", ready=False),
    SlashCommand("/policy", "Show the active policy", ready=False),
    SlashCommand("/compact", "Summarize the conversation to free context", ready=False),
    SlashCommand("/config", "Show configuration", ready=False),
)

SHORTCUTS = """\
Enter            Send
Ctrl+J, \\+Enter  New line
Up / Down        Move in the input, then browse history
Esc              Interrupt a run, or decline an approval
Esc Esc          Clear the input
Ctrl+C           Interrupt; when idle clear the input; press twice to quit
Ctrl+D           Quit (press twice)
Ctrl+O           Expand or collapse tool output and thinking
Ctrl+T           Show or hide the plan
Ctrl+S           Stash or restore the input
Ctrl+L           Redraw
Shift+Tab        Cycle mode
Option+T         Cycle reasoning effort (needs Option as Meta in your terminal)
PgUp / PgDn      Scroll the transcript (also the mouse wheel or trackpad)
Alt+Up / Alt+Down  Scroll three lines
Ctrl+Home / End  Jump to the top / the latest message
Drag             Select text; releasing copies it. Ctrl+C or Cmd+C copies too
/                Slash commands
?                This list (on an empty input)"""


def names() -> tuple[str, ...]:
    return tuple(c.name for c in CATALOG)


def help_text() -> str:
    width = max(len(c.name) for c in CATALOG)
    lines = [
        f"{c.name:<{width}}  {c.summary}" + ("" if c.ready else "  (not available yet)")
        for c in CATALOG
    ]
    return "\n".join(lines)
