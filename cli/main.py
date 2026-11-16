from __future__ import annotations

import argparse
import sys
import uuid
import warnings

# google-genai ships docstrings that trigger SyntaxWarning on 3.13; breaks curses TUI if printed.
warnings.filterwarnings("ignore", category=SyntaxWarning)

from cli.async_util import run_async
from cli.env import bootstrap_env
from cli.chat_cmd import cmd_chat
from cli.repl import run_repl
from cli.status import cmd_status
from cli.gmail_ingest_cmd import run_gmail_full_ingest
from cli.sync_cmd import run_sync
from backend.common.config_loader.settings import reload_settings


def build_parser() -> argparse.ArgumentParser:
    """CLI argument definitions for ``mindos``."""
    p = argparse.ArgumentParser(
        prog="mindos",
        description="MindOS — sync and process ingested context (terminal-first).",
    )
    p.add_argument("--sync", action="store_true", help="Run ingest sync (Gmail + future sources)")
    p.add_argument("--gmail", action="store_true", help="With --sync: Gmail only (default: Gmail when enabled)")
    p.add_argument(
        "--process",
        action="store_true",
        help="After sync, drain the outbox once (embeddings / commitments)",
    )
    sub = p.add_subparsers(dest="command")
    sync_p = sub.add_parser("sync", help="Same as --sync")
    sync_p.add_argument("--gmail", action="store_true")
    sync_p.add_argument("--process", action="store_true")
    sub.add_parser("status", help="Show connection and artifact counts")
    ingest_p = sub.add_parser(
        "gmail-ingest",
        help="Backfill Gmail (excludes Promotions tab and List-Unsubscribe mail)",
    )
    ingest_p.add_argument(
        "--query",
        type=str,
        default=None,
        help="Gmail search query override (default: no promotions / spam / unsubscribe)",
    )
    ingest_p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N messages (1 = single-mail smoke test with tqdm)",
    )
    ingest_p.add_argument(
        "--process",
        action="store_true",
        help="After ingest, drain the outbox once (embeddings / commitments)",
    )
    chat_p = sub.add_parser("chat", help="Interactive chat over local context")
    chat_p.add_argument("--session", type=str, default=None, help="Resume a chat session UUID")
    return p


def main() -> None:
    """Dispatch subcommands, flags, or the interactive REPL when argv is empty."""
    bootstrap_env()
    reload_settings()
    args = build_parser().parse_args()
    if getattr(args, "command", None) == "status":
        raise SystemExit(cmd_status())
    if getattr(args, "command", None) == "gmail-ingest":
        code = run_async(
            run_gmail_full_ingest(
                process=getattr(args, "process", False),
                query=getattr(args, "query", None),
                limit=int(getattr(args, "limit", 0) or 0),
            )
        )
        raise SystemExit(code)
    if getattr(args, "command", None) == "chat":
        sid = None
        raw = getattr(args, "session", None)
        if raw:
            try:
                sid = uuid.UUID(raw)
            except ValueError:
                print(f"invalid --session uuid: {raw}", file=sys.stderr)
                raise SystemExit(2)
        raise SystemExit(cmd_chat(session_id=sid))
    do_sync = args.sync or getattr(args, "command", None) == "sync"
    if do_sync:
        gmail_only = args.gmail or True
        process = args.process or getattr(args, "process", False)
        code = run_async(run_sync(gmail_only=gmail_only, process=process))
        raise SystemExit(code)
    if len(sys.argv) == 1:
        raise SystemExit(run_repl())
    build_parser().print_help()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
