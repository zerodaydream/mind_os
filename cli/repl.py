"""Default ``mindos`` REPL: main menu plus shared asyncio loop for nested commands."""

from __future__ import annotations

from cli import term
from cli.async_util import start_shared_event_loop, stop_shared_event_loop
from cli.chat_cmd import cmd_chat
from cli.env import bootstrap_env
from cli.async_util import run_async
from cli.status import cmd_status
from cli.sync_cmd import run_sync


def _dispatch_main_menu_choice(choice: str) -> bool:
    """Run one menu action. Returns False to exit REPL."""
    if choice == "exit":
        term.note("bye")
        return False
    if choice == "status":
        term.tui_busy(cmd_status, label="Loading status")
        return True
    if choice == "chat":
        cmd_chat()
        return True
    if choice in {"sync", "sync:process"}:
        process = choice == "sync:process"
        code = term.tui_busy(
            lambda: run_async(run_sync(gmail_only=True, process=process)),
            label="Syncing Gmail",
        )
        if code != 0:
            term.error("sync failed — see messages above")
        return True
    term.error(f"unknown choice: {choice!r}")
    return True


def run_repl() -> int:
    """Bootstrap env, run the interactive menu until exit, then tear down the event loop."""
    home = bootstrap_env()
    start_shared_event_loop()
    try:
        subtitle = f"project: {home}" if home else "Set MINDOS_HOME for global mindos"
        return term.run_interactive(subtitle, _dispatch_main_menu_choice)
    finally:
        stop_shared_event_loop()
