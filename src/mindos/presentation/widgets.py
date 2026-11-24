"""Small real widgets: spinner, hint line, status line, prompt input."""

from __future__ import annotations

import time

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static, TextArea

from mindos.presentation.tokens import PALETTE, Glyphs
from mindos.presentation.viewmodel import SessionInfo, ViewState

_VERBS = (
    "Thinking",
    "Working",
    "Pondering",
    "Checking",
    "Sorting",
    "Gathering",
    "Reading",
    "Weighing",
)
_VERB_SECONDS = 9.0
_SHOW_ELAPSED_AFTER = 2.0


def compact_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class SpinnerLine(Static):
    """`✻ Verb… (12s · ↓ 1.2k tokens · esc to interrupt)` while a run is working."""

    def __init__(self, *, plain: bool = False, id: str | None = None) -> None:
        super().__init__("", id=id)
        self._plain = plain
        self._glyphs = Glyphs.ascii() if plain else Glyphs()
        self._started: float | None = None
        self._frame = 0
        self._runs_seen = 0

    def tick(self, state: ViewState) -> None:
        if state.waiting_for_approval:
            self._started = None
            self.update(Text(f"{self._glyphs.info} Waiting for your approval", style=PALETTE.warn))
            return
        if not state.running:
            self._started = None
            self.update("")
            return
        if self._started is None:
            self._started = time.monotonic()
            self._runs_seen += 1
        elapsed = time.monotonic() - self._started
        self._frame += 1
        self.update(self._line(state, elapsed))

    def _line(self, state: ViewState, elapsed: float) -> Text:
        g = self._glyphs
        frames = g.spinner
        verb = _VERBS[(self._runs_seen + int(elapsed // _VERB_SECONDS)) % len(_VERBS)]
        glyph = frames[0] if self._plain else frames[(self._frame // 2) % len(frames)]
        text = Text()
        text.append(f"{glyph} ", style=Style(color=PALETTE.accent))
        label = f"{verb}{g.ellipsis}"
        if self._plain:
            text.append(label, style=Style(color=PALETTE.text))
        else:
            hot = self._frame % (len(label) + 6) - 3  # a moving highlight across the verb
            for i, ch in enumerate(label):
                bright = abs(i - hot) <= 1
                text.append(
                    ch, style=Style(color=PALETTE.text if bright else PALETTE.dim, bold=bright)
                )
        parts = []
        if elapsed >= _SHOW_ELAPSED_AFTER:
            parts.append(f"{int(elapsed)}s")
        if state.output_tokens:
            parts.append(f"{g.down} {compact_tokens(state.output_tokens)} tokens")
        parts.append("esc to interrupt")
        text.append(f" ({' · '.join(parts)})", style=Style(color=PALETTE.dim))
        return text


class HintLine(Static):
    """Mode label or `? for shortcuts`, slash suggestions, and the new-output cue."""

    def show(
        self,
        state: ViewState,
        *,
        suggestions: tuple[str, ...] = (),
        at_tail: bool = True,
        notice: str | None = None,
    ) -> None:
        dim = Style(color=PALETTE.dim)
        text = Text(no_wrap=True, overflow="ellipsis")
        if notice:
            text.append(notice, style=Style(color=PALETTE.warn))
        elif suggestions:
            text.append("  ".join(suggestions) + "   (tab to complete)", style=dim)
        elif state.mode.value != "default":
            text.append(f"{state.mode.value} mode on", style=Style(color=PALETTE.accent))
            text.append(" (shift+tab to cycle)", style=dim)
        else:
            text.append("? for shortcuts", style=dim)
        if not at_tail:
            text.append("   ↓ new output below (pgdn to follow)", style=Style(color=PALETTE.accent))
        self.update(text)


class StatusLine(Static):
    """Model, reasoning effort, approvals, context, cost. Integrations and jobs join later."""

    def show(self, state: ViewState, info: SessionInfo) -> None:
        parts: list[str] = []
        if info.models:
            parts.append(info.models[0])
        if state.effort is not None:
            parts.append(f"think {state.effort.value}")
        if state.reasoning_tokens:
            parts.append(f"reasoning {compact_tokens(state.reasoning_tokens)} tok")
        if state.approvals:
            n = len(state.approvals)
            parts.append(f"{n} approval{'s' if n != 1 else ''} pending")
        if state.degraded:
            parts.append(f"degraded: {', '.join(sorted(state.degraded))}")
        if state.context_pct is not None:
            parts.append(f"ctx {state.context_pct:.0f}%")
        if state.cost_usd is not None:
            parts.append(f"${state.cost_usd:.4f}")
        self.update(
            Text(
                " · ".join(parts), style=Style(color=PALETTE.dim), no_wrap=True, overflow="ellipsis"
            )
        )


class PromptInput(TextArea):
    """Multiline prompt. Enter submits; `\\`+Enter, Ctrl+J and Shift+Enter add a newline."""

    MAX_ROWS = 8

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class ShortcutsRequested(Message):
        pass

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(
            "",
            id=id,
            show_line_numbers=False,
            soft_wrap=True,
            tab_behavior="focus",
            compact=True,
        )
        self.slash_commands: tuple[str, ...] = ()
        self._history: list[str] = []
        self._cursor_in_history: int | None = None
        self._draft = ""

    def remember(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._cursor_in_history = None

    def clear_input(self) -> None:
        self.text = ""
        self._cursor_in_history = None
        self._fit_height()

    def set_text(self, text: str) -> None:
        self.text = text
        self.move_cursor(self.document.end)
        self._fit_height()

    async def _on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            event.stop()
            event.prevent_default()
            if self.text.endswith("\\") and self.cursor_location == self.document.end:
                self.text = self.text[:-1]
                self.move_cursor(self.document.end)
                self.insert("\n")
            else:
                self.post_message(self.Submitted(self.text))
            return
        if key in ("ctrl+j", "shift+enter"):
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        if key == "tab":
            event.stop()
            event.prevent_default()
            self._complete_slash()
            return
        if key == "question_mark" and not self.text:
            event.stop()
            event.prevent_default()
            self.post_message(self.ShortcutsRequested())
            return
        if key == "up" and self.cursor_location[0] == 0 and self._history:
            event.stop()
            event.prevent_default()
            self._history_step(-1)
            return
        if key == "down" and self.cursor_location[0] >= self.document.line_count - 1:
            event.stop()
            event.prevent_default()
            self._history_step(1)
            return
        await super()._on_key(event)

    def slash_suggestions(self) -> tuple[str, ...]:
        text = self.text
        if not text.startswith("/") or " " in text or "\n" in text:
            return ()
        return tuple(c for c in self.slash_commands if c.startswith(text))

    def _complete_slash(self) -> None:
        matches = self.slash_suggestions()
        if matches:
            self.set_text(matches[0] + " ")

    def _history_step(self, direction: int) -> None:
        if self._cursor_in_history is None:
            if direction > 0:
                return
            self._draft = self.text
            self._cursor_in_history = len(self._history)
        index = self._cursor_in_history + direction
        if index < 0:
            return
        if index >= len(self._history):
            self._cursor_in_history = None
            self.set_text(self._draft)
            return
        self._cursor_in_history = index
        self.set_text(self._history[index])

    def _on_text_area_changed(self, _: TextArea.Changed) -> None:
        self._fit_height()

    def _fit_height(self) -> None:
        rows = max(1, min(self.wrapped_document.height, self.MAX_ROWS))
        self.styles.height = rows + (0 if self.compact else 2)
