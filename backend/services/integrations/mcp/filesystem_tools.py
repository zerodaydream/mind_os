"""Host filesystem tools (allowed roots only; hazardous ops need user confirmation)."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from backend.common.langsmith_setup import trace_tool
from backend.services.agents.core.render.partial import PartialFn, emit_partial
from backend.services.graph.core.deep_session import mindos_home

_MAX_READ_BYTES = 120_000
_MAX_FIND = 80
_MAX_LIST = 200
_SPOTLIGHT_TIMEOUT_SEC = 20
_SKIP_WALK_DIRNAMES = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "venv", ".npm", "Pods"}
)
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


def _stem_matches_filename(filename: str, query: str) -> bool:
    stem = Path(filename).stem.casefold()
    q = query.casefold()
    if stem == q:
        return True
    return stem.startswith(q) and len(stem) > len(q) and stem[len(q)] in " (._-"


def _walk_collect_files(
    bases: list[Path],
    *,
    limit: int,
    accept_name: Callable[[str], bool],
) -> list[str]:
    hits: list[str] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(
                base, topdown=True, followlinks=False
            ):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_WALK_DIRNAMES]
                for fn in filenames:
                    if not accept_name(fn):
                        continue
                    resolved = _resolve_allowed(str(Path(dirpath) / fn))
                    if isinstance(resolved, Path) and resolved.is_file():
                        s = str(resolved)
                        if s not in hits:
                            hits.append(s)
                    if len(hits) >= limit:
                        return hits
        except OSError:
            continue
    return hits


def _python_find_in_dir(
    base: Path,
    raw_pat: str,
    glob_pat: str,
    *,
    stem_query: bool,
    exact_simple: bool,
    scoped: bool,
    hits: list[str],
    limit: int,
) -> None:
    """Pathlib iterdir/glob in one folder; walk only when searching a wide tree."""

    def add(path: Path) -> bool:
        resolved = _resolve_allowed(str(path))
        if not isinstance(resolved, Path) or not resolved.is_file():
            return len(hits) >= limit
        line = str(resolved)
        if line not in hits:
            hits.append(line)
        return len(hits) >= limit

    if not base.is_dir():
        return

    if stem_query:
        if scoped:
            try:
                for p in base.rglob("*"):
                    if p.is_file() and _stem_matches_filename(p.name, raw_pat) and add(p):
                        return
            except OSError:
                return
        for line in _walk_collect_files(
            [base],
            limit=limit,
            accept_name=lambda fn: _stem_matches_filename(fn, raw_pat),
        ):
            if line not in hits:
                hits.append(line)
            if len(hits) >= limit:
                return
        return

    if exact_simple:
        if scoped:
            try:
                direct = base / raw_pat
                if direct.is_file():
                    add(direct)
                    return
                for p in base.iterdir():
                    if p.is_file() and p.name.casefold() == raw_pat.casefold() and add(p):
                        return
            except OSError:
                return
        want = raw_pat.casefold()
        for line in _walk_collect_files(
            [base],
            limit=limit,
            accept_name=lambda fn, w=want: fn.casefold() == w,
        ):
            if line not in hits:
                hits.append(line)
            if len(hits) >= limit:
                return
        return

    pat = glob_pat[3:] if scoped and glob_pat.startswith("**/") else glob_pat
    try:
        for p in base.glob(pat):
            if p.is_file() and add(p):
                return
    except OSError:
        return


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


def _collect_spotlight_lines(stdout: str, *, limit: int, hits: list[Path]) -> None:
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        resolved = _resolve_allowed(line)
        if not isinstance(resolved, Path) or not resolved.is_file():
            continue
        if resolved in hits:
            continue
        hits.append(resolved)
        if len(hits) >= limit:
            return


def _run_mdfind_queries(queries: list[list[str]], *, limit: int) -> list[Path]:
    hits: list[Path] = []
    for cmd in queries:
        if len(hits) >= limit:
            break
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_SPOTLIGHT_TIMEOUT_SEC,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        _collect_spotlight_lines(proc.stdout, limit=limit, hits=hits)
    return hits


def _spotlight_paths(name: str, *, limit: int) -> list[Path]:
    if sys.platform != "darwin":
        return []
    safe = name.replace("\\", "\\\\").replace('"', '\\"')
    queries: list[list[str]] = [
        ["mdfind", f'kMDItemFSName == "{safe}"c'],
        ["mdfind", "-name", name],
    ]
    return _run_mdfind_queries(queries, limit=limit)


def _mdfind_scoped(
    base: Path,
    raw_pat: str,
    *,
    stem_query: bool,
    limit: int,
) -> list[Path]:
    """Spotlight limited to one folder (macOS); falls back to Python when index is stale."""
    if sys.platform != "darwin" or not base.is_dir():
        return []
    onlyin = str(base)
    if stem_query:
        safe = raw_pat.replace("\\", "\\\\").replace('"', '\\"')
        queries = [
            ["mdfind", "-onlyin", onlyin, f'kMDItemFSName == "*{safe}*"c'],
            ["mdfind", "-onlyin", onlyin, raw_pat],
        ]
    else:
        safe = raw_pat.replace("\\", "\\\\").replace('"', '\\"')
        queries = [
            ["mdfind", "-onlyin", onlyin, f'kMDItemFSName == "{safe}"c'],
            ["mdfind", "-onlyin", onlyin, "-name", raw_pat],
        ]
    return _run_mdfind_queries(queries, limit=limit)


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


def resolve_path_from_user_task(path: str, user_task: str) -> str:
    raw = (path or "").strip()
    if not raw or "/" in raw or raw.startswith("~"):
        return raw
    folder = resolve_folder_from_user_text(user_task)
    if folder is not None:
        return str(folder / raw)
    return raw


def _is_whole_disk_root(path: Path) -> bool:
    try:
        return path.resolve() == Path("/").resolve()
    except OSError:
        return False


def _find_path_label(path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path.home())
        if not rel.parts:
            return "home folder"
        return f"folder ~/{rel}"
    except ValueError:
        if path.resolve() == Path("/").resolve():
            return "entire disk"
        return f"folder {path.name or path}"


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


def _find_progress(
    cb: PartialFn | None,
    where: str,
    *,
    found: bool | None,
    list_only: bool = False,
) -> None:
    if cb is None:
        return
    if found is None:
        verb = "Listing" if list_only else "Searching"
        emit_partial(cb, f"→ {verb} {where}…", phase="think")
    elif found:
        emit_partial(cb, f"→ {where} — found!", phase="think")
    else:
        emit_partial(cb, f"→ {where} — not found", phase="think")


def _fmt_entry(path: Path) -> str:
    try:
        st = path.lstat()
    except OSError as exc:
        return f"{path.name}\t(error: {exc})"
    mode = "dir" if stat.S_ISDIR(st.st_mode) else "file"
    return f"{path.name}\t{mode}\t{st.st_size}"


def build_filesystem_tools(*, on_partial: PartialFn | None = None, user_task: str = ""):
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
        simple = _is_simple_filename(raw_pat) and not stem_query
        glob_pat = _prepare_find_pattern(raw_pat)
        inferred_dir = resolve_folder_from_user_text(user_task)
        root_base: Path | None = None
        if root.strip() and root.strip() not in {"/", "\\"}:
            resolved = _resolve_allowed(root.strip())
            if isinstance(resolved, str):
                return resolved
            if not resolved.is_dir():
                return f"Not a directory: {resolved}"
            if not _is_whole_disk_root(resolved):
                root_base = resolved
        narrow = root_base if root_base is not None else inferred_dir
        deep = deep and narrow is None
        deep = deep or (
            bool(_filesystem_config().get("deep_search_on_miss")) and narrow is None
        )

        scoped = False
        if root_base is not None:
            scan_dirs = [root_base]
            scoped = True
        elif inferred_dir is not None:
            if not inferred_dir.is_dir():
                return f"Not a directory: {inferred_dir}"
            scan_dirs = [inferred_dir]
            scoped = True
        elif deep:
            scan_dirs = _deep_search_dirs()
        else:
            scan_dirs = _fast_search_dirs()

        hits: list[str] = []
        ping = on_partial
        if simple and not scoped and sys.platform == "darwin":
            hits = [str(p) for p in _spotlight_paths(raw_pat, limit=cap)]

        for base in scan_dirs:
            if len(hits) >= cap:
                break
            label = _find_path_label(base)
            _find_progress(ping, label, found=None, list_only=scoped)
            n_before = len(hits)
            if scoped and sys.platform == "darwin":
                for p in _mdfind_scoped(
                    base, raw_pat, stem_query=stem_query, limit=cap - len(hits)
                ):
                    line = str(p)
                    if line not in hits:
                        hits.append(line)
                    if len(hits) >= cap:
                        break
            if len(hits) < cap:
                _python_find_in_dir(
                    base,
                    raw_pat,
                    glob_pat,
                    stem_query=stem_query,
                    exact_simple=simple,
                    scoped=scoped,
                    hits=hits,
                    limit=cap,
                )
            _find_progress(ping, label, found=len(hits) > n_before, list_only=scoped)

        if not hits:
            searched = ", ".join(str(r) for r in scan_dirs)
            hint = (
                " Re-run with deep=True for a slower full-disk scan."
                if not deep and not root.strip()
                else " Pass root= to search one folder."
            )
            return f"No matches for {raw_pat!r} (searched {searched}).{hint}"
        if len(hits) >= cap:
            hits.append(f"… stopped at {cap} results; narrow with root= or a tighter pattern.")
        return "\n".join(hits)

    async def mindos_fs_locate(
        name: str = "",
        max_results: int = 40,
    ) -> str:
        """Locate files for the user's request (folder + name from their words). One call—do not chain mindos_fs_find."""
        raw = (name or "").strip() or _guess_find_pattern(user_task) or ""
        if not raw:
            return "Could not infer a filename; pass name= (e.g. my_photo or report.pdf)."
        return await _run_find(raw, max_results=max_results)

    async def mindos_fs_find(
        pattern: str,
        root: str = "",
        max_results: int = 40,
        deep: bool = False,
    ) -> str:
        """Glob or advanced find only. For natural locate/count requests use mindos_fs_locate instead."""
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

    return [
        trace_tool(mindos_fs_roots),
        trace_tool(mindos_fs_list),
        trace_tool(mindos_fs_locate),
        trace_tool(mindos_fs_find),
        trace_tool(mindos_fs_read),
        trace_tool(mindos_fs_write),
        trace_tool(mindos_fs_rename),
        trace_tool(mindos_fs_delete),
    ]