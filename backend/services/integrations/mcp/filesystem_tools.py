"""Host filesystem tools (allowed roots only; hazardous ops need user confirmation)."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import stat
from pathlib import Path

from backend.common.langsmith_setup import trace_tool
from backend.services.agents.core.render.chat_stream import ChatStreamCallback
from backend.services.graph.core.deep_session import mindos_home
from backend.services.integrations.mcp.fs_ripgrep import (
    _MAX_SCAN_BASES,
    rg_grep,
    rg_list_files,
)

_MAX_READ_BYTES = 120_000
_MAX_FIND = 80
_MAX_GLOB = 80
_MAX_GREP_LINES = 200
_MAX_LIST = 200
_PREVIEW_CONFIRM = (
    "Ask the user to confirm this change, then call again with confirmed=True."
)


def _split_roots(raw: str) -> list[str]:
    if not raw.strip():
        return []
    if os.pathsep in raw:
        return [p.strip() for p in raw.split(os.pathsep) if p.strip()]
    return [p.strip() for p in raw.split(",") if p.strip()]


def _default_fs_root() -> Path:
    if os.name == "nt":
        drive = (os.environ.get("SystemDrive") or "C:").rstrip("\\") + "\\"
        return Path(drive).resolve()
    return Path("/").resolve()


def _filesystem_config() -> dict:
    from backend.common.config_loader import get_config

    raw = get_config().service_settings("agents").get("filesystem")
    return raw if isinstance(raw, dict) else {}


def filesystem_enabled() -> bool:
    return bool(_filesystem_config().get("enabled", True))


def filesystem_roots() -> list[Path]:
    env = os.environ.get("MINDOS_FS_ROOTS", "").strip()
    if env:
        parts = _split_roots(env)
    else:
        cfg_roots = _filesystem_config().get("roots")
        if isinstance(cfg_roots, list):
            parts = [str(r) for r in cfg_roots if str(r).strip()]
        else:
            parts = [str(_default_fs_root())]
    roots: list[Path] = []
    seen: set[str] = set()
    for part in parts:
        try:
            resolved = Path(part).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots or [_default_fs_root()]


def roots_summary() -> str:
    return ", ".join(str(r) for r in filesystem_roots())


def _resolve_allowed(path_str: str) -> Path | str:
    raw = (path_str or "").strip()
    if not raw:
        return "Path is required."
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = mindos_home() / p
    try:
        resolved = p.resolve()
    except (OSError, RuntimeError) as exc:
        return f"Invalid path: {exc}"
    for root in filesystem_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    return f"Path outside allowed roots ({roots_summary()}): {resolved}"


def _is_simple_filename(pattern: str) -> bool:
    pat = (pattern or "").strip()
    return (
        bool(pat)
        and "/" not in pat
        and "**" not in pat
        and "*" not in pat
        and "?" not in pat
        and "[" not in pat
    )


def _normalize_find_pattern(pattern: str) -> str | None:
    raw = (pattern or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"[\*\?\.\s]+", raw):
        return None
    if "*" in raw and "**" not in raw and "?" not in raw and "/" not in raw:
        core = raw.rstrip("*").rstrip(".")
        if core:
            return core
    return raw


def _is_bare_stem_query(pattern: str) -> bool:
    """Name fragment without extension, e.g. my_photo → my_photo.jpg, my_photo (1).png."""
    pat = (pattern or "").strip()
    return _is_simple_filename(pat) and "." not in pat and "*" not in pat


def _prepare_find_pattern(pattern: str) -> str:
    pat = (pattern or "").strip()
    if not pat:
        return pat
    if "**" in pat:
        return pat
    if any(c in pat for c in "*?["):
        return f"**/{pat}" if "/" not in pat else pat
    if "/" not in pat:
        return f"**/{pat}"
    return pat


def _fast_search_dirs() -> list[Path]:
    cfg = _filesystem_config().get("search_paths")
    if isinstance(cfg, list) and cfg:
        out: list[Path] = []
        seen: set[str] = set()
        for item in cfg:
            p = _resolve_allowed(str(Path(str(item)).expanduser()))
            if isinstance(p, Path) and p.is_dir():
                key = str(p)
                if key not in seen:
                    seen.add(key)
                    out.append(p)
        if out:
            return out
    # Default: workspace (MINDOS_HOME/cwd), not all of ~ — personal files need explicit path or search_paths.
    project = mindos_home()
    resolved = _resolve_allowed(str(project))
    if isinstance(resolved, Path) and resolved.is_dir():
        return [resolved]
    home = Path.home()
    return [home] if home.is_dir() else []


def _deep_search_dirs() -> list[Path]:
    return _walk_search_roots([r for r in filesystem_roots() if r.is_dir()])


def _walk_search_roots(roots: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if root == Path("/").resolve():
            for candidate in (Path.home(), Path("/Users"), Path("/Volumes"), root):
                key = str(candidate)
                if key not in seen and candidate.is_dir():
                    seen.add(key)
                    out.append(candidate)
        elif root.is_dir():
            key = str(root)
            if key not in seen:
                seen.add(key)
                out.append(root)
    return out or roots


_FOLDER_FUZZY_CUTOFF = 0.72


def _list_home_dirs() -> list[Path]:
    home = Path.home()
    if not home.is_dir():
        return []
    out: list[Path] = []
    try:
        entries = list(home.iterdir())
    except OSError:
        return []
    for p in entries:
        if not p.name or p.name.startswith("."):
            continue
        try:
            if not p.is_dir():
                continue
        except OSError:
            continue
        resolved = _resolve_allowed(str(p))
        if isinstance(resolved, Path):
            out.append(resolved)
    return out


def _match_home_dir(query: str) -> Path | None:
    q = (query or "").strip()
    if len(q) < 2:
        return None
    dirs = _list_home_dirs()
    if not dirs:
        return None
    qf = q.casefold()
    for d in dirs:
        if d.name.casefold() == qf:
            return d
    ranked = sorted(
        dirs,
        key=lambda d: difflib.SequenceMatcher(None, qf, d.name.casefold()).ratio(),
        reverse=True,
    )
    best = ranked[0]
    best_ratio = difflib.SequenceMatcher(None, qf, best.name.casefold()).ratio()
    if best_ratio < _FOLDER_FUZZY_CUTOFF:
        return None
    if len(ranked) > 1:
        second_ratio = difflib.SequenceMatcher(None, qf, ranked[1].name.casefold()).ratio()
        if second_ratio >= best_ratio - 0.05:
            return None
    return best


def _extract_location_queries(text: str) -> list[str]:
    scrubbed = re.sub(r"\b\S+\.\w{2,6}\b", " ", text or "")
    patterns = (
        r"\b(?:in|from|under|inside|on|at)\s+(?:my\s+|the\s+)?"
        r"([a-z][a-z0-9_-]{0,48}(?:\s+[a-z][a-z0-9_-]{0,32})?)"
        r"(?:\s+(?:folder|directory|dir))?\b",
        r"\b([a-z][a-z0-9_-]{2,48})\s+(?:folder|directory|dir)\b",
    )
    out: list[str] = []
    seen: set[str] = set()
    for pat in patterns:
        for m in re.finditer(pat, scrubbed, flags=re.IGNORECASE):
            q = m.group(1).strip()
            key = q.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
    return out


def resolve_folder_from_user_text(text: str) -> Path | None:
    """Map natural language to a real directory under the user's home (allowed roots)."""
    if not (text or "").strip():
        return None
    for query in _extract_location_queries(text):
        hit = _match_home_dir(query)
        if hit:
            return hit
    for m in re.finditer(r"(~[/\\][\w.\s-]+|/[\w./\s-]+)", text):
        raw = m.group(1).strip().rstrip(".,;")
        resolved = _resolve_allowed(str(Path(raw).expanduser()))
        if isinstance(resolved, Path) and resolved.is_dir():
            return resolved
    return None


_SKIP_GUESS_NAMES = frozenset(
    {"all", "files", "file", "many", "there", "any", "some", "the", "my", "in"}
)


def _guess_find_pattern(user_task: str) -> str | None:
    text = (user_task or "").strip()
    if not text:
        return None
    for pat in (
        r"\b([\w][\w.-]*\.\w{2,6})\b",
        r"\b([\w][\w.-]{0,48})\s+files?\b",
        r"\b(?:find|locate|count|list)\s+(?:all\s+)?(?:the\s+)?([\w][\w.-]{0,48})\b",
    ):
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        cand = m.group(1).strip()
        if cand.casefold() in _SKIP_GUESS_NAMES:
            continue
        norm = _normalize_find_pattern(cand)
        if norm:
            return norm
    return None


def resolve_path_from_user_task(path: str, user_task: str) -> str:
    raw = (path or "").strip()
    if not raw or "/" in raw or raw.startswith("~"):
        return raw
    folder = resolve_folder_from_user_text(user_task)
    if folder is not None:
        return str(folder / raw)
    return raw


def _default_search_dir() -> Path:
    fast = _fast_search_dirs()
    if fast:
        return fast[0]
    roots = filesystem_roots()
    return roots[0] if roots else Path.home()


def _resolve_search_dir(path_str: str) -> Path | str:
    raw = (path_str or "").strip()
    if not raw or raw in {".", "./"}:
        return _default_search_dir()
    resolved = _resolve_allowed(raw)
    if isinstance(resolved, str):
        return resolved
    if resolved.is_file():
        return resolved.parent
    if not resolved.is_dir():
        return f"Not a directory: {resolved}"
    return resolved


def _glob_patterns_for_find(raw_pat: str, glob_pat: str, *, stem_query: bool) -> list[str]:
    if stem_query:
        return [f"**/*{raw_pat}*"]
    if any(c in raw_pat for c in "*?[]"):
        return [glob_pat]
    if "/" in raw_pat:
        return [raw_pat if raw_pat.startswith("**/") else f"**/{raw_pat}"]
    return [glob_pat]


def _fmt_entry(path: Path) -> str:
    try:
        st = path.lstat()
    except OSError as exc:
        return f"{path.name}\t(error: {exc})"
    mode = "dir" if stat.S_ISDIR(st.st_mode) else "file"
    return f"{path.name}\t{mode}\t{st.st_size}"


def build_filesystem_tools(
    *,
    on_partial: ChatStreamCallback | None = None,
    user_task: str = "",
    scout_only: bool = False,
):
    async def mindos_fs_roots() -> str:
        """List directory roots the agent may read or modify."""
        return f"Allowed filesystem roots:\n" + "\n".join(f"- {r}" for r in filesystem_roots())

    async def mindos_fs_list(path: str = ".", max_entries: int = 50) -> str:
        """List files and folders under path (within allowed roots)."""
        resolved = _resolve_allowed(path)
        if isinstance(resolved, str):
            return resolved
        if not resolved.is_dir():
            return f"Not a directory: {resolved}"
        cap = max(1, min(int(max_entries), _MAX_LIST))
        try:
            entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as exc:
            return f"List failed: {exc}"
        if not entries:
            return f"(empty) {resolved}"
        lines = [f"{resolved}/", *(_fmt_entry(e) for e in entries[:cap])]
        if len(entries) > cap:
            lines.append(f"… +{len(entries) - cap} more (narrow path or raise max_entries)")
        return "\n".join(lines)

    def _accept_existing_file(path_str: str) -> Path | str:
        resolved = _resolve_allowed(path_str)
        if isinstance(resolved, str):
            return resolved
        if resolved.is_file():
            return resolved
        return "not a file"

    async def _run_find(
        pattern: str,
        *,
        root: str = "",
        max_results: int = _MAX_FIND,
        deep: bool = False,
    ) -> str:
        raw_pat = _normalize_find_pattern(pattern)
        if raw_pat is None:
            return (
                "pattern too broad (e.g. * alone); use a name stem like my_photo "
                "or an exact filename like my_photo.jpg."
            )
        cap = max(1, min(int(max_results), _MAX_FIND))
        stem_query = _is_bare_stem_query(raw_pat)
        glob_pat = _prepare_find_pattern(raw_pat)
        globs = _glob_patterns_for_find(raw_pat, glob_pat, stem_query=stem_query)

        if root.strip() in {"/", "\\"}:
            return (
                "Pick a folder (e.g. ~/Downloads), not /. "
                "Use deep=True only to scan all allowed roots."
            )

        scan_dirs: list[Path] = []
        if root.strip():
            resolved = _resolve_search_dir(root)
            if isinstance(resolved, str):
                return resolved
            scan_dirs = [resolved]
        elif deep:
            scan_dirs = _deep_search_dirs()
        else:
            scan_dirs = _fast_search_dirs()

        skipped_roots = 0
        if len(scan_dirs) > _MAX_SCAN_BASES:
            skipped_roots = len(scan_dirs) - _MAX_SCAN_BASES
            scan_dirs = scan_dirs[:_MAX_SCAN_BASES]

        hits: list[str] = []
        errors: list[str] = []
        for base in scan_dirs:
            if len(hits) >= cap:
                break
            batch, err = rg_list_files(
                base,
                globs,
                limit=cap - len(hits),
                accept_path=_accept_existing_file,
            )
            if err:
                errors.append(err)
            for line in batch:
                if line not in hits:
                    hits.append(line)
        if not hits:
            searched = ", ".join(str(r) for r in scan_dirs)
            hint = " Retry with root= on a subfolder, or deep=True across allowed roots."
            err_bit = f" ({errors[0]})" if errors else ""
            return f"No matches for {raw_pat!r} under {searched}.{err_bit}{hint}"
        if len(hits) >= cap:
            hits.append(f"… stopped at {cap} results; narrow root= or pattern.")
        if skipped_roots:
            hits.append(
                f"… deep scan capped at {_MAX_SCAN_BASES} roots ({skipped_roots} skipped); pass root=."
            )
        return "\n".join(hits)

    async def mindos_fs_grep(
        pattern: str,
        path: str = "",
        glob: str = "",
        output_mode: str = "content",
        head_limit: int = _MAX_GREP_LINES,
        B: int = 0,
        A: int = 0,
        C: int = 0,
        i: bool = False,
    ) -> str:
        """Search file contents with ripgrep. Start under path (default home); narrow path/glob and call again."""
        base = _resolve_search_dir(path)
        if isinstance(base, str):
            return base
        return rg_grep(
            pattern,
            base,
            file_glob=glob,
            output_mode=output_mode,
            head_limit=head_limit,
            context_before=B,
            context_after=A,
            context_both=C,
            ignore_case=i,
            accept_path=_accept_existing_file,
        )

    async def mindos_fs_glob(
        glob_pattern: str,
        target_directory: str = "",
        max_results: int = _MAX_GLOB,
    ) -> str:
        """List file paths matching a glob (ripgrep --files). Call repeatedly with tighter globs/dirs."""
        pat = (glob_pattern or "").strip()
        if not pat:
            return "glob_pattern is required (e.g. **/*.py or **/budget.xlsx)."
        cap = max(1, min(int(max_results), _MAX_GLOB))
        base = _resolve_search_dir(target_directory)
        if isinstance(base, str):
            return base
        patterns = [pat if pat.startswith("**/") or "/" in pat else f"**/{pat}"]
        hits, err = rg_list_files(
            base,
            patterns,
            limit=cap,
            accept_path=_accept_existing_file,
        )
        if err and not hits:
            return err
        if not hits:
            return f"No files for glob {pat!r} under {base}"
        if len(hits) >= cap:
            hits.append(f"… stopped at {cap}; narrow target_directory or glob_pattern.")
        return "\n".join(hits)

    async def mindos_fs_locate(
        name: str,
        root: str = "",
        max_results: int = 40,
    ) -> str:
        """Find files by name under root. Prefer mindos_fs_glob / mindos_fs_list + mindos_fs_grep for exploration."""
        raw = (name or "").strip()
        if not raw:
            return "pass name= (e.g. my_photo or report.pdf) and root= when you know the folder."
        return await _run_find(raw, root=root, max_results=max_results)

    async def mindos_fs_find(
        pattern: str,
        root: str = "",
        max_results: int = 40,
        deep: bool = False,
    ) -> str:
        """Filename glob under root (default home). Same as locate; use mindos_fs_glob for explicit ** patterns."""
        return await _run_find(pattern, root=root, max_results=max_results, deep=deep)

    async def mindos_fs_read(path: str, max_chars: int = 24_000) -> str:
        """Read a text document (utf-8). Use for local files; URLs use mindos_summarize_url."""
        resolved = _resolve_allowed(path)
        if isinstance(resolved, str):
            return resolved
        if not resolved.is_file():
            return f"Not a file: {resolved}"
        limit = max(500, min(int(max_chars), _MAX_READ_BYTES))
        try:
            data = resolved.read_bytes()
        except OSError as exc:
            return f"Read failed: {exc}"
        if len(data) > _MAX_READ_BYTES:
            return (
                f"File too large ({len(data)} bytes, cap {_MAX_READ_BYTES}). "
                "Ask the user to narrow the path or read a smaller excerpt."
            )
        text = data.decode("utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit] + f"\n… [truncated at {limit} chars]"
        return text

    async def files_find(
        query: str,
        root: str = "",
        max_results: int = 12,
        deep: bool = False,
    ) -> str:
        """Find local files by name under configured roots (file-scout primary tool).

        Prefer an explicit root (~/Downloads, ~/Desktop, ~/Documents). Never use /.
        Returns absolute paths; keep answers concise for the lead agent.
        """
        q = (query or "").strip()
        if not q:
            return (
                "query required (filename stem or glob). "
                "Example: query='invoice.pdf' root='~/Downloads'."
            )
        if root.strip() in {"/", "\\"}:
            return (
                "Refuse whole-disk scan. Pass root=~/Downloads|Desktop|Documents "
                "or omit for home-scoped search."
            )
        result = await _run_find(q, root=root, max_results=max(1, min(max_results, 24)), deep=deep)
        if result.startswith("No ") or result.startswith("pattern too"):
            return (
                f"{result}\n"
                "Next: try another root, a shorter stem, or deep=True only if the user asked "
                "for a wide scan."
            )
        # Annotate for subagent → lead handoff (one-line reasons).
        lines = []
        for line in result.splitlines():
            p = line.strip()
            if not p or p.startswith("…") or p.startswith("Pick"):
                lines.append(p)
                continue
            reason = _path_reason(p)
            lines.append(f"{p} — {reason}" if reason else p)
        return "\n".join(lines)

    async def files_read(path: str, max_chars: int = 8_000) -> str:
        """Range-limited local file read for confirming a file-scout candidate."""
        cap = max(500, min(int(max_chars), 24_000))
        return await mindos_fs_read(path, max_chars=cap)

    async def mindos_fs_write(
        path: str,
        content: str,
        confirmed: bool = False,
    ) -> str:
        """Write or overwrite a text file. Overwriting an existing file requires confirmed=True."""
        resolved = _resolve_allowed(path)
        if isinstance(resolved, str):
            return resolved
        if resolved.exists() and not confirmed:
            kind = "directory" if resolved.is_dir() else "file"
            return (
                f"Would overwrite existing {kind}: {resolved} ({resolved.stat().st_size} bytes). "
                + _PREVIEW_CONFIRM
            )
        resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved.write_text(content or "", encoding="utf-8")
        except OSError as exc:
            return f"Write failed: {exc}"
        return f"Wrote {len(content or '')} chars to {resolved}"

    async def mindos_fs_rename(
        src: str,
        dest: str,
        confirmed: bool = False,
    ) -> str:
        """Move or rename a file or directory. Dest must stay in allowed roots; collisions need confirmed=True."""
        s = _resolve_allowed(src)
        d = _resolve_allowed(dest)
        if isinstance(s, str):
            return s
        if isinstance(d, str):
            return d
        if not s.exists():
            return f"Source missing: {s}"
        if d.exists() and not confirmed:
            return f"Would replace existing path: {d}. " + _PREVIEW_CONFIRM
        d.parent.mkdir(parents=True, exist_ok=True)
        try:
            s.rename(d)
        except OSError as exc:
            return f"Rename failed: {exc}"
        return f"Renamed {s} -> {d}"

    async def mindos_fs_delete(
        path: str,
        recursive: bool = False,
        confirmed: bool = False,
    ) -> str:
        """Delete a file or directory. Always preview first (confirmed=False), then confirmed=True after user OK."""
        resolved = _resolve_allowed(path)
        if isinstance(resolved, str):
            return resolved
        if not resolved.exists():
            return f"Already gone: {resolved}"
        if not confirmed:
            if resolved.is_dir():
                n = sum(1 for _ in resolved.rglob("*")) if recursive else len(list(resolved.iterdir()))
                scope = f"directory ({n} entries under it)" if recursive else "non-empty directory"
                extra = " Pass recursive=True for trees." if resolved.is_dir() and not recursive else ""
                return f"Would delete {scope}: {resolved}{extra} " + _PREVIEW_CONFIRM
            return f"Would delete file: {resolved} ({resolved.stat().st_size} bytes). " + _PREVIEW_CONFIRM
        try:
            if resolved.is_dir():
                if recursive:
                    shutil.rmtree(resolved)
                else:
                    resolved.rmdir()
            else:
                resolved.unlink()
        except OSError as exc:
            return f"Delete failed: {exc}"
        return f"Deleted {resolved}"

    if scout_only:
        return [
            trace_tool(files_find),
            trace_tool(files_read),
            trace_tool(mindos_fs_roots),
            trace_tool(mindos_fs_list),
            trace_tool(mindos_fs_glob),
            trace_tool(mindos_fs_grep),
            trace_tool(mindos_fs_locate),
            trace_tool(mindos_fs_find),
            trace_tool(mindos_fs_read),
        ]
    return [
        trace_tool(files_find),
        trace_tool(files_read),
        trace_tool(mindos_fs_roots),
        trace_tool(mindos_fs_list),
        trace_tool(mindos_fs_grep),
        trace_tool(mindos_fs_glob),
        trace_tool(mindos_fs_locate),
        trace_tool(mindos_fs_find),
        trace_tool(mindos_fs_read),
        trace_tool(mindos_fs_write),
        trace_tool(mindos_fs_rename),
        trace_tool(mindos_fs_delete),
    ]


def _path_reason(path_str: str) -> str:
    """One-line mtime/size hint for file-scout handoff."""
    try:
        p = Path(path_str).expanduser()
        if not p.is_file():
            return "candidate"
        st = p.stat()
        from datetime import datetime, timezone

        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")
        kb = max(1, st.st_size // 1024)
        return f"{kb}KB, mtime {mtime}"
    except OSError:
        return "candidate"