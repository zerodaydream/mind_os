"""A small modal list: move with the arrow keys, mouse, or trackpad; Enter chooses; Esc cancels."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


class PickerScreen(ModalScreen[str | None]):
    """Dismisses with the chosen value, or None when cancelled."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("escape", "cancel", "Cancel", show=False)]

    def __init__(
        self,
        title: str,
        options: Sequence[tuple[str, str]],
        *,
        current: str | None = None,
        hint: str = "↑/↓ move   Enter choose   Esc cancel",
    ) -> None:
        super().__init__()
        self._title = title
        self._options = list(options)
        self._current = current
        self._hint = hint

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(self._title, id="picker-title")
            yield OptionList(
                *(
                    Option(("● " if value == self._current else "  ") + label, id=value)
                    for label, value in self._options
                ),
                id="picker-list",
            )
            yield Static(self._hint, id="picker-hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        values = [value for _, value in self._options]
        options.highlighted = values.index(self._current) if self._current in values else 0
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)
