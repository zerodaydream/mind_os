"""Local file format conversion (allowed roots only; source files are never modified)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from backend.common.langsmith_setup import trace_tool
from backend.services.integrations.mcp.filesystem_tools import (
    _resolve_allowed,
    filesystem_enabled,
    resolve_path_from_user_task,
)

_PREVIEW_CONFIRM = (
    "Ask the user to confirm this conversion, then call again with confirmed=True."
)
_MAX_BATCH = 40
_CONVERT_TIMEOUT_SEC = 300

_IMAGE_EXTS = frozenset(
    {"png", "jpg", "jpeg", "webp", "gif", "bmp", "tiff", "tif", "heic", "heif", "avif", "ico"}
)
_OFFICE_EXTS = frozenset(
    {
        "pdf",
        "doc",
        "docx",
        "odt",
        "rtf",
        "txt",
        "xls",
        "xlsx",
        "ods",
        "csv",
        "ppt",
        "pptx",
        "odp",
        "html",
        "htm",
    }
)
_PANDOC_EXTS = frozenset({"md", "markdown", "rst", "tex", "latex", "epub"})
_AUDIO_EXTS = frozenset(
    {"mp3", "wav", "flac", "aac", "m4a", "ogg", "opus", "wma", "aiff", "aif", "caf", "alac"}
)
_VIDEO_EXTS = frozenset(
    {"mp4", "mov", "mkv", "webm", "avi", "m4v", "mpeg", "mpg", "wmv", "flv", "3gp"}
)
_FFMPEG_EXTS = _IMAGE_EXTS | _AUDIO_EXTS | _VIDEO_EXTS


def convert_enabled() -> bool:
    return filesystem_enabled()


def _normalize_fmt(fmt: str) -> str:
    f = (fmt or "").strip().lower().lstrip(".")
    aliases = {"jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md"}
    return aliases.get(f, f)


def _ext(path: Path) -> str:
    return _normalize_fmt(path.suffix)


def _which(*names: str) -> str | None:
    for name in names:
        if p := shutil.which(name):
            return p
    return None


def _ffmpeg_bin() -> str | None:
    if p := _which("ffmpeg"):
        return p
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if Path(exe).is_file() else None
    except ImportError:
        return None


def _soffice_bin() -> str | None:
    direct = _which("soffice", "libreoffice")
    if direct:
        return direct
    mac = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    if mac.is_file():
        return str(mac)
    return None


def _backends() -> dict[str, bool]:
    return {
        "convx": bool(_which("convx", "convx-mcp")),
        "libreoffice": bool(_soffice_bin()),
        "ffmpeg": bool(_ffmpeg_bin()),
        "sips": sys.platform == "darwin",
        "pandoc": bool(_which("pandoc")),
        "pdftoppm": bool(_which("pdftoppm")),
    }


def _default_dest(src: Path, to_fmt: str) -> Path:
    return src.with_name(f"{src.stem}.{to_fmt}")


def _allocate_dest(preferred: Path, reserved: set[str] | None = None) -> Path:
    """Pick preferred, or stem (1).ext, (2)… if taken on disk or in this batch."""
    reserved = reserved or set()
    parent, stem, suffix = preferred.parent, preferred.stem, preferred.suffix
    n = 0
    while n < 10_000:
        name = f"{stem}{suffix}" if n == 0 else f"{stem} ({n}){suffix}"
        cand = parent / name
        if not cand.exists() and str(cand) not in reserved:
            return cand
        n += 1
    return parent / f"{stem} ({n}){suffix}"


def _unique_dest(path: Path) -> Path:
    return _allocate_dest(path)


def _run(cmd: list[str], *, timeout: int = _CONVERT_TIMEOUT_SEC) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    err = (proc.stderr or proc.stdout or "").strip()
    return proc.returncode, err[-2000:] if err else ""


def _convx_bin() -> str | None:
    return _which("convx", "convx-mcp")


def _try_convx(src: Path, dest: Path, to_fmt: str) -> str | None:
    bin_ = _convx_bin()
    if not bin_:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [bin_, "convert", str(src), "--to", to_fmt]
    if dest != _default_dest(src, to_fmt):
        cmd.extend(["--output", str(dest)])
    code, err = _run(cmd)
    if code != 0:
        return f"convx failed ({code}): {err or 'unknown error'}"
    out = dest if dest.is_file() else _default_dest(src, to_fmt)
    if not out.is_file():
        return "convx reported success but output file is missing"
    if out != dest and dest != out:
        try:
            out.replace(dest)
        except OSError as exc:
            return f"convx output at {out} but could not move to {dest}: {exc}"
    return None


def _try_sips(src: Path, dest: Path, to_fmt: str) -> str | None:
    if sys.platform != "darwin":
        return None
    sips_fmt = {"jpg": "jpeg", "jpeg": "jpeg"}.get(to_fmt, to_fmt)
    if sips_fmt not in {"jpeg", "png", "gif", "tiff", "bmp", "pdf", "heic"}:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, err = _run(["sips", "-s", "format", sips_fmt, str(src), "--out", str(dest)], timeout=120)
    if code != 0:
        return f"sips failed ({code}): {err or 'unknown error'}"
    if not dest.is_file():
        return "sips reported success but output file is missing"
    return None


def _try_ffmpeg(src: Path, dest: Path, to_fmt: str) -> str | None:
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    from_ext = _ext(src)
    if from_ext not in _FFMPEG_EXTS and to_fmt not in _FFMPEG_EXTS:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src)]
    if to_fmt in _AUDIO_EXTS and from_ext in _AUDIO_EXTS | _VIDEO_EXTS:
        cmd.append("-vn")
    cmd.append(str(dest))
    code, err = _run(cmd)
    if code != 0:
        return f"ffmpeg failed ({code}): {err or 'unknown error'}"
    if not dest.is_file():
        return "ffmpeg reported success but output file is missing"
    return None


def _try_soffice(src: Path, dest: Path, to_fmt: str) -> str | None:
    soffice = _soffice_bin()
    if not soffice:
        return None
    from_ext = _ext(src)
    if from_ext not in _OFFICE_EXTS and to_fmt not in _OFFICE_EXTS:
        return None
    outdir = dest.parent
    outdir.mkdir(parents=True, exist_ok=True)
    convert_to = to_fmt
    if to_fmt == "csv":
        convert_to = 'csv:"Text - txt - csv (StarCalc)"'
    cmd = [soffice, "--headless", "--convert-to", convert_to, "--outdir", str(outdir), str(src)]
    if from_ext == "pdf" and to_fmt in {"docx", "doc", "odt"}:
        cmd.insert(-1, "--infilter")
        cmd.insert(-1, "writer_pdf_import")
    code, err = _run(cmd)
    if code != 0:
        return f"LibreOffice failed ({code}): {err or 'unknown error'}"
    produced = outdir / f"{src.stem}.{to_fmt}"
    if not produced.is_file():
        # LibreOffice may use a different extension for csv
        alt = outdir / f"{src.stem}.csv" if to_fmt == "csv" else None
        if alt and alt.is_file():
            produced = alt
        else:
            return "LibreOffice reported success but output file is missing"
    if produced != dest:
        if dest.exists():
            dest.unlink()
        try:
            produced.replace(dest)
        except OSError as exc:
            return f"LibreOffice wrote {produced} but could not move to {dest}: {exc}"
    return None


def _try_pandoc(src: Path, dest: Path, to_fmt: str) -> str | None:
    if not _which("pandoc"):
        return None
    from_ext = _ext(src)
    if from_ext not in _PANDOC_EXTS | _OFFICE_EXTS and to_fmt not in _PANDOC_EXTS | {"html", "md"}:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    code, err = _run(["pandoc", str(src), "-o", str(dest)])
    if code != 0:
        return f"pandoc failed ({code}): {err or 'unknown error'}"
    if not dest.is_file():
        return "pandoc reported success but output file is missing"
    return None


def _try_pdftoppm(src: Path, dest: Path, to_fmt: str) -> str | None:
    if not _which("pdftoppm") or _ext(src) != "pdf" or to_fmt not in {"png", "jpg", "jpeg"}:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    prefix = dest.parent / dest.stem
    fmt_flag = "-jpeg" if to_fmt in {"jpg", "jpeg"} else "-png"
    code, err = _run(["pdftoppm", fmt_flag, str(src), str(prefix)], timeout=600)
    if code != 0:
        return f"pdftoppm failed ({code}): {err or 'unknown error'}"
    first = dest.parent / f"{dest.stem}-1.{to_fmt if to_fmt != 'jpeg' else 'jpg'}"
    if not first.is_file():
        candidates = sorted(dest.parent.glob(f"{dest.stem}-1.*"))
        if not candidates:
            return "pdftoppm produced no pages"
        first = candidates[0]
    if first != dest:
        try:
            first.replace(dest)
        except OSError as exc:
            return f"pdftoppm wrote {first} but could not move to {dest}: {exc}"
    return None


def convert_file_core(src: Path, dest: Path, to_fmt: str) -> str:
    """Return success message or error string."""
    to_fmt = _normalize_fmt(to_fmt)
    from_fmt = _ext(src)
    if from_fmt == to_fmt:
        return f"Source is already {to_fmt}: {src}"
    if not src.is_file():
        return f"Not a file: {src}"
    last: str | None = None
    steps = [
        lambda: _try_convx(src, dest, to_fmt),
        lambda: _try_sips(src, dest, to_fmt)
        if from_fmt in _IMAGE_EXTS or to_fmt in _IMAGE_EXTS
        else None,
        lambda: _try_ffmpeg(src, dest, to_fmt),
        lambda: _try_pdftoppm(src, dest, to_fmt),
        lambda: _try_soffice(src, dest, to_fmt),
        lambda: _try_pandoc(src, dest, to_fmt),
    ]
    for attempt in steps:
        err = attempt()
        if err is None:
            if dest.is_file():
                return f"Converted {src} -> {dest} ({from_fmt} to {to_fmt})"
            continue
        if err.split()[0] in {"convx", "sips", "ffmpeg", "LibreOffice", "pandoc", "pdftoppm"}:
            return err
        last = err
    be = _backends()
    hints = []
    if not be["convx"]:
        hints.append("install ConvX (brew install convx) for 50+ formats in one tool")
    if not be["libreoffice"]:
        hints.append("install LibreOffice for office/PDF conversions")
    hint = f" Backends: {json.dumps(be)}." + (" " + "; ".join(hints) if hints else "")
    return (
        f"No local converter for {_ext(src)} -> {to_fmt}.{hint} "
        "Or install convx and retry."
    )


def build_convert_tools(*, user_task: str = ""):
    async def mindos_convert_capabilities() -> str:
        """List local conversion backends on this machine and typical format support."""
        be = _backends()
        lines = [
            "Local conversion uses the same filesystem roots as mindos_fs_*.",
            f"Backends detected: {json.dumps(be)}",
            "Routing: convx (if installed) else sips/ffmpeg (images + audio/video; ffmpeg from PATH or imageio-ffmpeg in cli extra), "
            "LibreOffice (office/PDF), pdftoppm (PDF page 1 to png/jpg), pandoc (markdown/HTML/text).",
            "For rare pairs, install https://github.com/JSBtechnologies/convx (`convx mcp` is also valid as a separate MCP).",
            "Outputs are new files beside the source (or output_dir); if the default output path exists, "
            "mindos_convert_file previews and asks before writing stem (1).ext. "
            "mindos_fs_glob or mindos_fs_locate → mindos_convert_file or mindos_convert_batch; "
            "rerun with confirmed=True after user OK.",
        ]
        return "\n".join(lines)

    async def mindos_convert_file(
        path: str,
        to_format: str,
        output_path: str = "",
        confirmed: bool = False,
    ) -> str:
        """Convert one file to to_format (e.g. jpg, png, pdf, docx, csv). New file beside source; folder from the user's words is resolved automatically."""
        resolved = _resolve_allowed(resolve_path_from_user_task(path, user_task))
        if isinstance(resolved, str):
            return resolved
        to_fmt = _normalize_fmt(to_format)
        if not to_fmt:
            return "to_format is required (e.g. jpg, pdf, docx, csv)."
        if output_path.strip():
            dest = _resolve_allowed(output_path.strip())
            if isinstance(dest, str):
                return dest
        else:
            dest = _default_dest(resolved, to_fmt)
            gate = _resolve_allowed(str(dest))
            if isinstance(gate, str):
                return gate
            dest = gate
        if dest.is_file() and not confirmed:
            alt = _unique_dest(dest)
            return (
                f"Output already exists: {dest}\n"
                f"Shall I create {alt}? {_PREVIEW_CONFIRM}"
            )
        if confirmed and dest.is_file():
            dest = _unique_dest(dest)
        return convert_file_core(resolved, dest, to_fmt)

    async def mindos_convert_batch(
        paths: list[str],
        to_format: str,
        output_dir: str = "",
        confirmed: bool = False,
    ) -> str:
        """Convert many files to the same format. Pass absolute paths from mindos_fs_find. Preview first, then confirmed=True."""
        to_fmt = _normalize_fmt(to_format)
        if not to_fmt:
            return "to_format is required."
        if not paths:
            return "paths must be a non-empty list of file paths."
        if len(paths) > _MAX_BATCH:
            return f"At most {_MAX_BATCH} paths per batch; split the job."
        resolved_out: Path | None = None
        if output_dir.strip():
            out = _resolve_allowed(output_dir.strip())
            if isinstance(out, str):
                return out
            if not out.is_dir():
                return f"output_dir is not a directory: {out}"
            resolved_out = out
        plan: list[tuple[Path, Path]] = []
        taken: set[str] = set()
        for raw in paths:
            src = _resolve_allowed(str(raw).strip())
            if isinstance(src, str):
                return src
            if resolved_out:
                dest = resolved_out / f"{src.stem}.{to_fmt}"
                gate = _resolve_allowed(str(dest))
                if isinstance(gate, str):
                    return gate
                dest = gate
            else:
                dest = _default_dest(src, to_fmt)
                gate = _resolve_allowed(str(dest))
                if isinstance(gate, str):
                    return gate
                dest = gate
            dest = _allocate_dest(dest, taken)
            taken.add(str(dest))
            plan.append((src, dest))
        if not confirmed:
            lines = [f"Would convert {len(plan)} file(s) to .{to_fmt} (new files only; sources unchanged):"]
            for src, dest in plan:
                lines.append(f"  {src} -> {dest}")
            lines.append(_PREVIEW_CONFIRM)
            return "\n".join(lines)
        results: list[str] = []
        for src, dest in plan:
            results.append(convert_file_core(src, dest, to_fmt))
        ok = sum(1 for r in results if r.startswith("Converted"))
        return f"Batch done: {ok}/{len(plan)} succeeded.\n" + "\n".join(results)

    return [
        trace_tool(mindos_convert_capabilities),
        trace_tool(mindos_convert_file),
        trace_tool(mindos_convert_batch),
    ]