"""Live mic STT for the TUI (optional ``voice`` extra — Moonshine)."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Any

_LOCK = threading.Lock()
_MIC: Any = None
_LINES: list[str] = []
_CURRENT = ""
_ON_PARTIAL: Callable[[str], None] | None = None
_MISSING = "voice extra missing — uv sync --extra voice"


def stt_status() -> tuple[bool, str]:
    try:
        import moonshine_voice  # noqa: F401
    except ImportError:
        return False, _MISSING
    return True, ""


def recorder_options() -> dict[str, Any]:
    return {
        "language": os.getenv("MINDOS_STT_LANG", "en"),
        "backend": "moonshine",
    }


def is_warmed() -> bool:
    with _LOCK:
        return _MIC is not None


def warm() -> None:
    """Load model from disk cache into RAM (blocking, once per chat session)."""
    _mic()


def _transcript() -> str:
    parts = list(_LINES)
    if _CURRENT:
        parts.append(_CURRENT)
    return " ".join(parts).strip()


def _listener(event: Any) -> None:
    global _CURRENT
    from moonshine_voice.transcriber import LineCompleted, LineTextChanged

    if isinstance(event, LineTextChanged):
        _CURRENT = (event.line.text or "").strip()
    elif isinstance(event, LineCompleted):
        t = (event.line.text or "").strip()
        if t and (not _LINES or _LINES[-1] != t):
            _LINES.append(t)
        _CURRENT = ""
    else:
        return
    cb = _ON_PARTIAL
    if cb:
        text = _transcript()
        if text:
            cb(text)


def _mic() -> Any:
    global _MIC
    with _LOCK:
        if _MIC is not None:
            return _MIC
        from moonshine_voice import MicTranscriber

        interval = float(os.getenv("MINDOS_STT_UPDATE_S", "0.2"))
        mic = MicTranscriber().language(recorder_options()["language"]).update_interval(
            interval
        )
        mic.load()
        _MIC = mic
        return _MIC


def start_listen(on_partial: Callable[[str], None]) -> None:
    global _ON_PARTIAL, _LINES, _CURRENT
    _LINES = []
    _CURRENT = ""
    _ON_PARTIAL = on_partial
    mic = _mic()
    mic.remove_all_listeners()
    mic.add_listener(_listener)
    mic.start()


def stop_listen() -> None:
    mic = _MIC
    if mic is not None:
        mic.stop()


def shutdown() -> None:
    global _MIC, _ON_PARTIAL, _LINES, _CURRENT
    stop_listen()
    with _LOCK:
        if _MIC is not None:
            _MIC.close()
        _MIC = None
    _ON_PARTIAL = None
    _LINES = []
    _CURRENT = ""