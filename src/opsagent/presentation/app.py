"""The terminal UI application (ARCHITECTURE section 13, docs/phases/03-tui.md).

Events flow `bus -> pending list -> reduce (30 Hz) -> ViewState -> widgets`. Widgets call only
application commands. Nothing here imports business, provider, or persistence code.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from opsagent.application import Application, Subscription
from opsagent.domain import AppEvent, ApprovalMode, ReasoningEffort
from opsagent.domain.commands import SetMode, SubmitMessage
from opsagent.presentation import slash, viewmodel
from opsagent.presentation.approval import ApprovalDialog
from opsagent.presentation.tokens import PALETTE
from opsagent.presentation.transcript import Transcript
from opsagent.presentation.viewmodel import SessionInfo, ViewState
from opsagent.presentation.widgets import HintLine, PromptInput, SpinnerLine, StatusLine

_DOUBLE_ESC_SECONDS = 0.6
_EXIT_CONFIRM_SECONDS = 2.0
_MODES = tuple(ApprovalMode)


class OpsApp(App[None]):
    CSS_PATH = "theme/ops.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "interrupt", "Interrupt", priority=True, show=False),
        Binding("ctrl+c", "ctrl_c", "Interrupt or quit", priority=True, show=False),
        Binding("ctrl+d", "ctrl_d", "Quit", priority=True, show=False),
        Binding("ctrl+o", "toggle_expanded", "Expand", priority=True, show=False),
        Binding("ctrl+t", "toggle_todos", "Plan", priority=True, show=False),
        Binding("ctrl+s", "stash", "Stash", priority=True, show=False),
        Binding("ctrl+l", "redraw", "Redraw", priority=True, show=False),
        Binding("shift+tab", "cycle_mode", "Mode", priority=True, show=False),
        Binding("alt+t,†", "cycle_effort", "Effort", priority=True, show=False),
        Binding("pageup", "scroll_up", "Scroll up", priority=True, show=False),
        Binding("pagedown", "scroll_down", "Scroll down", priority=True, show=False),
    ]

    def __init__(
        self, application: Application, info: SessionInfo, *, flush_hz: float = 30.0
    ) -> None:
        super().__init__()
        self._application = application
        self._session = info
        self._flush_interval = 1.0 / flush_hz
        self.state: ViewState = viewmodel.initial_state(info)
        self._event_buffer: list[AppEvent] = []
        self._needs_apply = True
        self._subscription_cm: AbstractContextManager[Subscription] | None = None
        self._approval_dialog: ApprovalDialog | None = None
        self._last_esc = 0.0
        self._exit_armed_until = 0.0
        self._stash = ""
        self._hint_notice: tuple[str, float] | None = None

    # ---- layout ----------------------------------------------------------------------------
    def get_css_variables(self) -> dict[str, str]:
        return {**super().get_css_variables(), **PALETTE.css_variables()}

    def compose(self) -> ComposeResult:
        yield Transcript(plain=self._session.plain, id="transcript")
        yield SpinnerLine(plain=self._session.plain, id="spinner")
        yield Vertical(id="approval-host")
        with Horizontal(id="prompt-box"):
            yield Static("> ", id="prompt-glyph")
            yield PromptInput(id="prompt")
        yield HintLine(id="hint")
        yield StatusLine(id="status")

    # ---- lifecycle -------------------------------------------------------------------------
    def on_mount(self) -> None:
        self.prompt.slash_commands = slash.names()
        self.prompt.focus()
        self._subscription_cm = self._application.bus.subscribe()
        subscription = self._subscription_cm.__enter__()
        self.run_worker(self._consume(subscription), name="events", exit_on_error=False)
        self.set_interval(self._flush_interval, self._drain_events)
        self.set_interval(0.1, self._tick_ui)

    async def on_unmount(self) -> None:
        if self._subscription_cm is not None:
            self._subscription_cm.__exit__(None, None, None)
            self._subscription_cm = None
        await self._application.aclose()

    async def _consume(self, subscription: Subscription) -> None:
        async for event in subscription:
            self._event_buffer.append(event)

    # ---- widgets ---------------------------------------------------------------------------
    @property
    def prompt(self) -> PromptInput:
        return self.query_one("#prompt", PromptInput)

    @property
    def transcript(self) -> Transcript:
        return self.query_one("#transcript", Transcript)

    # ---- state flow ------------------------------------------------------------------------
    def _set_state(self, fn: Callable[[ViewState], ViewState]) -> None:
        self.state = fn(self.state)
        self._needs_apply = True

    def _drain_events(self) -> None:
        if self._event_buffer:
            batch, self._event_buffer = self._event_buffer, []
            for event in batch:
                self.state = viewmodel.reduce(self.state, event)
            self._needs_apply = True
        if self._needs_apply:
            self._needs_apply = False
            self._apply_state()

    def _apply_state(self) -> None:
        s = self.state
        self.transcript.set_view(s.rows, expanded=s.expanded, todos_visible=s.todos_visible)
        self.query_one("#status", StatusLine).show(s, self._session)
        self._sync_approval()
        self._refresh_hint()

    def _tick_ui(self) -> None:
        self.query_one("#spinner", SpinnerLine).tick(self.state)
        if self._hint_notice is not None and time.monotonic() > self._hint_notice[1]:
            self._hint_notice = None
            self._refresh_hint()

    def _refresh_hint(self) -> None:
        notice = self._hint_notice[0] if self._hint_notice else None
        self.query_one("#hint", HintLine).show(
            self.state,
            suggestions=self.prompt.slash_suggestions(),
            at_tail=self.transcript.at_tail,
            notice=notice,
        )

    def _notify_hint(self, text: str, seconds: float = _EXIT_CONFIRM_SECONDS) -> None:
        self._hint_notice = (text, time.monotonic() + seconds)
        self._refresh_hint()

    def _sync_approval(self) -> None:
        wanted = self.state.approvals[0].view if self.state.approvals else None
        current = self._approval_dialog
        if wanted is not None and (current is None or current.approval_id != wanted.approval_id):
            if current is not None:
                current.remove()
            self._approval_dialog = ApprovalDialog(wanted, plain=self._session.plain)
            self.query_one("#approval-host").mount(self._approval_dialog)
            self.prompt.disabled = True
        elif wanted is None and current is not None:
            current.remove()
            self._approval_dialog = None
            self.prompt.disabled = False
            self.prompt.focus()

    # ---- input -----------------------------------------------------------------------------
    def on_text_area_changed(self) -> None:
        self._refresh_hint()

    def on_prompt_input_submitted(self, message: PromptInput.Submitted) -> None:
        text = message.text.strip()
        if not text:
            return
        self.prompt.remember(text)
        self.prompt.clear_input()
        if text.startswith("/"):
            self._run_slash(text)
            return
        self._set_state(lambda s: viewmodel.add_user_message(s, text))
        self._application.submit_message(
            SubmitMessage(text=text, conversation_id=self.state.conversation_id)
        )
        self.transcript.jump_to_tail()

    def on_prompt_input_shortcuts_requested(self) -> None:
        self._set_state(lambda s: viewmodel.add_command_output(s, "?", slash.SHORTCUTS))

    def on_approval_dialog_decided(self, message: ApprovalDialog.Decided) -> None:
        if not self._application.decide(message.decision):
            self._set_state(lambda s: viewmodel.add_notice(s, "warning", "That approval is gone."))

    # ---- actions ---------------------------------------------------------------------------
    async def action_interrupt(self) -> None:
        if self._approval_dialog is not None:
            self._approval_dialog.decline_or_back()
            return
        if self.state.running:
            await self._cancel_runs()
            return
        now = time.monotonic()
        if now - self._last_esc < _DOUBLE_ESC_SECONDS:
            self._last_esc = 0.0
            if self.prompt.text:
                self.prompt.remember(self.prompt.text)
                self.prompt.clear_input()
            else:
                self._set_state(
                    lambda s: viewmodel.add_notice(s, "dim", "Rewind is not available yet.")
                )
            return
        self._last_esc = now

    async def action_ctrl_c(self) -> None:
        if self.state.running:
            await self._cancel_runs()
        elif self.prompt.text:
            self.prompt.clear_input()
        elif time.monotonic() < self._exit_armed_until:
            await self._quit_app()
        else:
            self._exit_armed_until = time.monotonic() + _EXIT_CONFIRM_SECONDS
            self._notify_hint("Press Ctrl+C again to quit")

    async def action_ctrl_d(self) -> None:
        if self.prompt.text:
            self.prompt.action_delete_right()
        elif time.monotonic() < self._exit_armed_until:
            await self._quit_app()
        else:
            self._exit_armed_until = time.monotonic() + _EXIT_CONFIRM_SECONDS
            self._notify_hint("Press Ctrl+D again to quit")

    def action_toggle_expanded(self) -> None:
        self._set_state(viewmodel.toggle_expanded)

    def action_toggle_todos(self) -> None:
        self._set_state(viewmodel.toggle_todos)

    def action_stash(self) -> None:
        if self.prompt.text:
            self._stash = self.prompt.text
            self.prompt.clear_input()
            self._notify_hint("Input stashed. Press Ctrl+S to restore.")
        elif self._stash:
            self.prompt.set_text(self._stash)
            self._stash = ""

    def action_redraw(self) -> None:
        self.refresh(layout=True)
        self.screen.refresh(layout=True)

    def action_cycle_mode(self) -> None:
        nxt = _MODES[(_MODES.index(self.state.mode) + 1) % len(_MODES)]
        self._application.set_mode(SetMode(mode=nxt))

    def action_cycle_effort(self) -> None:
        levels = self._session.effort_levels
        if not levels:
            return
        current = self.state.effort
        index = levels.index(current) if current in levels else -1
        self._set_state(lambda s: viewmodel.set_effort(s, levels[(index + 1) % len(levels)]))

    def action_scroll_up(self) -> None:
        self.transcript.scroll_page_up(animate=False)

    def action_scroll_down(self) -> None:
        self.transcript.scroll_page_down(animate=False)

    async def _cancel_runs(self) -> None:
        for run_id in sorted(self.state.active_runs):
            await self._application.cancel(run_id)

    async def _quit_app(self) -> None:
        await self._application.aclose()
        self.exit()

    # ---- slash commands --------------------------------------------------------------------
    def _run_slash(self, text: str) -> None:
        name, _, args = text.partition(" ")
        args = args.strip()
        known = {c.name: c for c in slash.CATALOG}
        command = known.get(name)
        if command is None:
            self._emit_output(name, f"Unknown command {name}. Type /help.")
        elif not command.ready:
            self._emit_output(name, f"{name} is not available yet.")
        elif name == "/help":
            self._emit_output(name, slash.help_text() + "\n\n" + slash.SHORTCUTS)
        elif name == "/clear":
            self._set_state(lambda s: viewmodel.clear_transcript(s, self._session))
        elif name == "/exit":
            self.run_worker(self._quit_app(), name="quit", exit_on_error=False)
        elif name == "/think":
            self._slash_think(args)
        elif name == "/status":
            self._emit_output(name, self._status_text())
        elif name == "/cost":
            self._emit_output(name, self._cost_text())
        elif name == "/model":
            self._emit_output(name, "\n".join(self._session.models) or "No models configured.")

    def _emit_output(self, command: str, text: str) -> None:
        self._set_state(lambda s: viewmodel.add_command_output(s, command, text))

    def _slash_think(self, args: str) -> None:
        levels = self._session.effort_levels
        if not args:
            current = self.state.effort.value if self.state.effort else "default"
            allowed = ", ".join(e.value for e in levels)
            self._emit_output("/think", f"Reasoning effort is {current}. Options: {allowed}.")
            return
        try:
            effort = ReasoningEffort(args.lower())
        except ValueError:
            self._emit_output("/think", f"Unknown level '{args}'.")
            return
        if effort not in levels:
            self._emit_output("/think", f"This model does not support '{effort.value}'.")
            return
        self._set_state(lambda s: viewmodel.set_effort(s, effort))
        self._emit_output("/think", f"Reasoning effort set to {effort.value} for this session.")

    def _status_text(self) -> str:
        s = self.state
        lines = [
            f"version {self._session.version}",
            f"cwd {self._session.cwd}",
            f"models {', '.join(self._session.models) or 'none'}",
            f"effort {s.effort.value if s.effort else 'default'}",
            f"mode {s.mode.value}",
            f"active runs {len(s.active_runs)}",
            f"conversation {s.conversation_id or 'none yet'}",
        ]
        return "\n".join(lines)

    def _cost_text(self) -> str:
        s = self.state
        cost = f"${s.cost_usd:.4f}" if s.cost_usd is not None else "not reported"
        return (
            f"output tokens {s.output_tokens}\nreasoning tokens {s.reasoning_tokens}\ncost {cost}"
        )
