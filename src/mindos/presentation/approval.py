"""Approval panel: blocking, focused on the safe option (docs/phases/03-tui.md)."""

from __future__ import annotations

import json
from typing import Any

from rich.console import Group
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, Static

from mindos.domain import RiskClass
from mindos.domain.commands import Decide
from mindos.domain.events import ApprovalView
from mindos.presentation.tokens import PALETTE

_PREVIEW_LINES = 10
_CONSEQUENCE = {
    RiskClass.R0: "Read-only.",
    RiskClass.R1: "Low impact and easy to undo.",
    RiskClass.R2: "Changes data outside this machine.",
    RiskClass.R3: "Hard to undo.",
    RiskClass.R4: "Bulk or destructive. Review every item.",
}
_OPTIONS = (
    ("Yes", "a"),
    ("Yes, and edit the arguments first", "e"),
    ("No, and tell the agent what to do instead", "r"),
)
_SAFE = 2


class ApprovalDialog(Vertical, can_focus=True):
    """Arrow keys and Enter, number keys, or a / e / r. Esc declines. Tab adds a comment."""

    class Decided(Message):
        def __init__(self, decision: Decide) -> None:
            super().__init__()
            self.decision = decision

    def __init__(self, view: ApprovalView, *, plain: bool = False) -> None:
        super().__init__(id="approval")
        self._view = view
        self._plain = plain
        self._selected = _SAFE  # focus lands on the safe option
        self._stage = "choose"  # choose | edit | comment
        self._comment: str | None = None
        self._pending_choice = "stay"  # what a comment submission does: "reject" or "stay"
        self._done = False
        self.border_title = f"Approve · {view.tool}"

    @property
    def approval_id(self) -> str:
        return self._view.approval_id

    def compose(self) -> ComposeResult:
        yield Static(self._body(), id="approval-body")
        yield Static(self._options(), id="approval-options")
        yield Input(id="approval-input", classes="hidden")

    def on_mount(self) -> None:
        self.focus()

    # ---- rendering -------------------------------------------------------------------------
    def _body(self) -> Group:
        v = self._view
        risk_color = PALETTE.error if v.risk.rank >= 3 else PALETTE.warn
        head = Text.assemble(
            (f"[{v.risk.value}] ", Style(color=risk_color, bold=True)),
            (v.summary, Style(bold=True, color=PALETTE.text)),
        )
        lines = v.payload_preview.splitlines() or [""]
        shown = lines[:_PREVIEW_LINES]
        if len(lines) > _PREVIEW_LINES:
            shown.append(f"… +{len(lines) - _PREVIEW_LINES} more lines")
        parts: list[Text] = [head, Text("\n".join(shown), style=Style(color=PALETTE.dim))]
        if v.tainted:
            parts.append(
                Text(
                    "Built from untrusted content (email, web page, or file).",
                    style=Style(color=PALETTE.warn),
                )
            )
        parts.append(Text(_CONSEQUENCE[v.risk], style=Style(color=PALETTE.dim)))
        if v.expires_at is not None:
            parts.append(Text(f"Expires {v.expires_at:%H:%M:%S}", style=Style(color=PALETTE.dim)))
        parts.append(Text(f"payload {v.payload_hash}", style=Style(color=PALETTE.dim)))
        return Group(*parts)

    def _options(self) -> Text:
        text = Text()
        for i, (label, key) in enumerate(_OPTIONS):
            pointer = ">" if self._plain else "❯"
            active = i == self._selected and self._stage == "choose"
            style = Style(color=PALETTE.accent, bold=True) if active else Style(color=PALETTE.text)
            text.append(f"{pointer if active else ' '} {i + 1}. {label} ({key})\n", style=style)
        hint = {
            "choose": "Enter to confirm · esc to decline · tab to add a comment",
            "edit": "Edit the JSON arguments, then Enter to send. Esc goes back.",
            "comment": "Type a note for the agent, then Enter. Esc goes back.",
        }[self._stage]
        text.append(hint, style=Style(color=PALETTE.dim))
        return text

    def _refresh_options(self) -> None:
        self.query_one("#approval-options", Static).update(self._options())

    # ---- keys ------------------------------------------------------------------------------
    def on_key(self, event: events.Key) -> None:
        if self._done or self._stage != "choose":
            return
        key = event.key
        handled = True
        if key in ("up", "k"):
            self._selected = (self._selected - 1) % len(_OPTIONS)
        elif key in ("down", "j"):
            self._selected = (self._selected + 1) % len(_OPTIONS)
        elif key in ("a", "1"):
            self._finish("approve")
        elif key in ("e", "2"):
            self._begin_edit()
        elif key in ("r", "3"):
            self._begin_comment(then="reject")
        elif key == "enter":
            self._confirm_selected()
        elif key == "tab":
            self._begin_comment(then="stay")
        else:
            handled = False
        if handled:
            event.stop()
            event.prevent_default()
            self._refresh_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if self._stage == "edit":
            try:
                args = json.loads(event.value)
            except json.JSONDecodeError as exc:
                self.query_one("#approval-options", Static).update(
                    Text(
                        f"Not valid JSON: {exc.msg}. Fix it or press Esc.",
                        style=Style(color=PALETTE.error),
                    )
                )
                return
            if not isinstance(args, dict):
                self.query_one("#approval-options", Static).update(
                    Text("Arguments must be a JSON object.", style=Style(color=PALETTE.error))
                )
                return
            self._finish("edit", edited_args=args)
        elif self._stage == "comment":
            self._comment = event.value.strip() or None
            if self._pending_choice == "reject":
                self._finish("reject")
            else:
                self._end_input()

    def decline_or_back(self) -> None:
        """Esc: leave an input field, or decline the action. The app routes Esc here."""
        if self._done:
            return
        if self._stage == "choose":
            self._finish("reject")
        else:
            self._end_input()
            self._refresh_options()

    # ---- actions ---------------------------------------------------------------------------
    def _confirm_selected(self) -> None:
        if self._selected == 0:
            self._finish("approve")
        elif self._selected == 1:
            self._begin_edit()
        else:
            self._begin_comment(then="reject")

    def _begin_edit(self) -> None:
        try:
            parsed: Any = json.loads(self._view.payload_preview)
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            self.query_one("#approval-options", Static).update(
                Text(
                    "These arguments cannot be edited here. Approve or decline.",
                    style=Style(color=PALETTE.warn),
                )
            )
            return
        self._show_input(self._view.payload_preview, stage="edit")

    def _begin_comment(self, *, then: str) -> None:
        self._pending_choice = then
        self._show_input(self._comment or "", stage="comment")

    def _show_input(self, value: str, *, stage: str) -> None:
        self._stage = stage
        field = self.query_one("#approval-input", Input)
        field.value = value
        field.remove_class("hidden")
        field.focus()
        self._refresh_options()

    def _end_input(self) -> None:
        self._stage = "choose"
        field = self.query_one("#approval-input", Input)
        field.add_class("hidden")
        self.focus()
        self._refresh_options()

    def _finish(self, decision: str, *, edited_args: dict[str, Any] | None = None) -> None:
        if self._done:
            return
        self._done = True
        self.post_message(
            self.Decided(
                Decide(
                    approval_id=self._view.approval_id,
                    decision=decision,
                    edited_args=edited_args,
                    comment=self._comment,
                )
            )
        )
