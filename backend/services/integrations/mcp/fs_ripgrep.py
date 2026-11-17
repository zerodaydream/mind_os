"""Ripgrep-backed search; results stay inside allowed roots."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

_RG_WALL_SEC = 25
_MAX_SCAN_BASES = 3
_CURSOR_BUNDLED_RG = Path(
    "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg"
)
_SKIP_DIR_GLOBS = (
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".npm",
    "Pods",
)


def rg_binary() -> str:
    found = shutil.which("rg")
    if found:
        return found
    if _CURSOR_BUNDLED_RG.is_file():
        return str(_CURSOR_BUNDLED_RG)
    return "rg"


def rg_skip_dir_args() -> list[str]:
    out: list[str] = []
    for name in _SKIP_DIR_GLOBS:
        out.extend(["-g", f"!**/{name}/**"])
    return out


def _stop_rg(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def _stream_rg_lines(
    args: list[str],
    *,
    max_lines: int,
    wall_sec: float = _RG_WALL_SEC,
) -> tuple[list[str], str | None]:
    """Read rg stdout until enough lines or wall clock; then kill rg (not full-tree --files)."""
    cmd = [rg_binary(), *args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError as exc:
        return [], str(exc)
    deadline = time.monotonic() + wall_sec
    out: list[str] = []
    note: str | None = None
    try:
        if proc.stdout is None:
            _stop_rg(proc)
            return [], "ripgrep produced no output"
        for raw in proc.stdout:
            if time.monotonic() >= deadline:
                note = f"timed out after {int(wall_sec)}s; narrow path or glob"
                break
            line = raw.rstrip("\r\n")
            if not line:
                continue
            out.append(line)
            if len(out) >= max_lines:
                break
        else:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                note = note or f"timed out after {int(wall_sec)}s; narrow path or glob"
    finally:
        _stop_rg(proc)
    if len(out) > 10_000:
        out = out[:10_000]
        note = note or "output truncated"
    return out, note


def filter_lines(
    lines: str,
    *,
    limit: int,
    accept_path: Callable[[str], Path | str],
) -> tuple[list[str], str | None]:
    """Keep unique paths that pass accept_path (e.g. allowed-root check)."""
    hits: list[str] = []
    note: str | None = None
    for line in lines.splitlines():
        line = line.strip()
        if not line:
            continue
        resolved = accept_path(line)
        if isinstance(resolved, str):
            if note is None:
                note = resolved
            continue
        path = str(resolved)
        if path in hits:
            continue
        hits.append(path)
        if len(hits) >= limit:
            break
    return hits, note


def rg_list_files(
    base: Path,
    glob_patterns: list[str],
    *,
    limit: int,
    accept_path: Callable[[str], Path | str],
) -> tuple[list[str], str | None]:
    args: list[str] = [
        "--files",
        "--no-messages",
        "--max-filesize",
        "50M",
    ]
    for pat in glob_patterns:
        args.extend(["-g", pat])
    args.extend(rg_skip_dir_args())
    args.append(str(base))
    # Read extra raw lines — many are dropped by allowed-root filter.
    raw, stream_note = _stream_rg_lines(args, max_lines=max(limit * 8, limit + 20))
    hits, note = filter_lines("\n".join(raw), limit=limit, accept_path=accept_path)
    return hits, stream_note or note


def rg_grep(
    pattern: str,
    base: Path,
    *,
    file_glob: str,
    output_mode: str,
    head_limit: int,
    context_before: int,
    context_after: int,
    context_both: int,
    ignore_case: bool,
    accept_path: Callable[[str], Path | str],
) -> str:
    if not (pattern or "").strip():
        return "pattern is required."
    cap = max(1, min(int(head_limit), 2000))
    args: list[str] = ["--no-messages", "--max-columns", "500"]
    if ignore_case:
        args.append("-i")
    if context_both > 0:
        args.extend(["-C", str(context_both)])
    else:
        if context_before > 0:
            args.extend(["-B", str(context_before)])
        if context_after > 0:
            args.extend(["-A", str(context_after)])
    mode = (output_mode or "content").strip().lower()
    if mode == "files_with_matches":
        args.append("-l")
    elif mode == "count":
        args.append("-c")
    else:
        mode = "content"
    if file_glob.strip():
        args.extend(["-g", file_glob.strip()])
    args.extend(rg_skip_dir_args())
    args.extend(["--max-count", str(cap), "--max-filesize", "50M"])
    args.extend([pattern, str(base)])
    raw, stream_note = _stream_rg_lines(args, max_lines=cap * 4)
    out = "\n".join(raw)
    if not out.strip():
        msg = f"No matches for {pattern!r} under {base}"
        if stream_note and "timed out" in stream_note:
            return f"{msg} ({stream_note})"
        return msg
    if mode == "content":
        kept: list[str] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            file_part = line.split(":", 1)[0]
            resolved = accept_path(file_part)
            if isinstance(resolved, Path):
                kept.append(line)
            if len(kept) >= cap:
                kept.append(f"… stopped at {cap} lines; narrow path or pattern.")
                break
        if stream_note and "timed out" in stream_note:
            kept.append(f"({stream_note})")
        return "\n".join(kept) if kept else f"No matches under allowed roots for {pattern!r}"
    lines = out.strip().splitlines()
    if len(lines) > cap:
        lines = lines[:cap] + [f"… stopped at {cap} lines; narrow path or pattern."]
    if stream_note and "timed out" in stream_note:
        lines.append(f"({stream_note})")
    return "\n".join(lines)