"""Copy text to the system clipboard from a terminal app.

OSC 52 (which Textual sends) works in most modern terminals, but not in macOS Terminal.app, and
some terminals disable it. On macOS `pbcopy` always works, so it is used too. Presentation only:
the text is what the user selected on screen.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
from typing import Any

from textual.app import App

_TIMEOUT_S = 2.0


def copy_text(app: App[Any], text: str) -> None:
    app.copy_to_clipboard(text)  # OSC 52
    if sys.platform == "darwin" and (pbcopy := shutil.which("pbcopy")):
        with contextlib.suppress(OSError, subprocess.SubprocessError):  # OSC 52 may have worked
            subprocess.run([pbcopy], input=text.encode(), check=False, timeout=_TIMEOUT_S)
