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

from mindos.application import Application, Subscription
from mindos.domain import AppEvent, ApprovalMode, ModelChoice, ModelRole, ReasoningEffort
from mindos.domain.commands import SetMode, SubmitMessage
from mindos.presentation import slash, viewmodel
from mindos.presentation.approval import ApprovalDialog
from mindos.presentation.clipboard import copy_text
from mindos.presentation.picker import PickerScreen
from mindos.presentation.tokens import PALETTE
from mindos.presentation.transcript import Transcript
from mindos.presentation.viewmodel import SessionInfo, ViewState
from mindos.presentation.widgets import HintLine, PromptInput, SpinnerLine, StatusLine

_DOUBLE_ESC_SECONDS = 0.6
_EXIT_CONFIRM_SECONDS = 2.0
_MODES = tuple(ApprovalMode)


class MindOSApp(App[None]):
    CSS_PATH = "theme/mindos.tcss"
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
        Binding("alt+up", "scroll_line_up", "Scroll up a line", priority=True, show=False),
        Binding("alt+down", "scroll_line_down", "Scroll down a line", priority=True, show=False),
        Binding("ctrl+home", "scroll_top", "Top", priority=True, show=False),
        Binding("ctrl+end", "scroll_tail", "Latest", priority=True, show=False),
        Binding("super+c,ctrl+shift+c", "copy_selection", "Copy", priority=True, show=False),
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
        self._last_copied = ""
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
        # After subscribing, so approvals left pending by an earlier session appear as dialogs.
        self.run_worker(self._application.recover(), name="recover", exit_on_error=False)
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
        spinners = self.query("#spinner")
        if not spinners:
            return
        spinners.first(SpinnerLine).tick(self.state)
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
            SubmitMessage(
                text=text, conversation_id=self.state.conversation_id, effort=self.state.effort
            )
        )
        self.transcript.jump_to_tail()

    def on_prompt_input_shortcuts_requested(self) -> None:
        self._set_state(lambda s: viewmodel.add_command_output(s, "?", slash.SHORTCUTS))

    def on_approval_dialog_decided(self, message: ApprovalDialog.Decided) -> None:
        if not self._application.decide(message.decision):
            self._set_state(lambda s: viewmodel.add_notice(s, "warning", "That approval is gone."))

    # ---- actions ---------------------------------------------------------------------------
    async def action_interrupt(self) -> None:
        if isinstance(self.screen, PickerScreen):
            self.screen.action_cancel()
            return
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
        if self._copy_selection():  # with text selected, Ctrl+C copies, as everywhere else
            return
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

    def action_scroll_line_up(self) -> None:
        self.transcript.scroll_relative(y=-3, animate=False)

    def action_scroll_line_down(self) -> None:
        self.transcript.scroll_relative(y=3, animate=False)

    def action_scroll_top(self) -> None:
        self.transcript.scroll_home(animate=False)

    def action_scroll_tail(self) -> None:
        self.transcript.jump_to_tail()

    def action_copy_selection(self) -> None:
        if not self._copy_selection():
            self._notify_hint("Nothing selected. Drag over text to select it.")

    # ---- copy ------------------------------------------------------------------------------
    def on_mouse_up(self) -> None:
        """Releasing the mouse after a drag copies the selection (like most terminals)."""
        self._copy_selection(quiet_if_empty=True)

    def _selected_text(self) -> str:
        return (self.screen.get_selected_text() or self.prompt.selected_text or "").strip("\n")

    def _copy_selection(self, *, quiet_if_empty: bool = False) -> bool:
        text = self._selected_text()
        if not text or (text == self._last_copied and quiet_if_empty):
            return bool(text)
        self._last_copied = text
        copy_text(self, text)
        self._notify_hint(f"Copied {len(text)} characters")
        return True

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
        elif name == "/model":
            self._slash_model(args)
        elif name == "/status":
            self._emit_output(name, self._status_text())
        elif name == "/cost":
            self._emit_output(name, self._cost_text())

    def _emit_output(self, command: str, text: str) -> None:
        self._set_state(lambda s: viewmodel.add_command_output(s, command, text))

    # ---- model and effort ------------------------------------------------------------------
    def _slash_think(self, args: str) -> None:
        """`/think` opens a list; `/think high` sets the orchestrator; `/think subagent low`."""
        role, rest = self._split_role(args)
        if not rest:
            self.run_worker(self._pick_effort(role), name="pick-effort", exit_on_error=False)
            return
        try:
            effort = ReasoningEffort(rest.lower())
        except ValueError:
            self._emit_output("/think", f"Unknown level '{rest}'.")
            return
        self._choose(role or ModelRole.ORCHESTRATOR, effort=effort, command="/think")

    def _slash_model(self, args: str) -> None:
        """`/model` opens a list; `/model <id>` sets the orchestrator; `/model subagent <id>`."""
        role, rest = self._split_role(args)
        if not rest:
            self.run_worker(self._pick_model(role), name="pick-model", exit_on_error=False)
            return
        self._choose(role or ModelRole.ORCHESTRATOR, model_id=rest, command="/model")

    @staticmethod
    def _split_role(args: str) -> tuple[ModelRole | None, str]:
        first, _, rest = args.strip().partition(" ")
        aliases = {
            "orchestrator": ModelRole.ORCHESTRATOR,
            "main": ModelRole.ORCHESTRATOR,
            "subagent": ModelRole.SUBAGENT,
            "subagents": ModelRole.SUBAGENT,
            "sub": ModelRole.SUBAGENT,
        }
        role = aliases.get(first.lower())
        return (role, rest.strip()) if role else (None, args.strip())

    def _current(self, role: ModelRole) -> ModelChoice:
        tuning = self._application.tuning
        if tuning is not None:
            return tuning.current(role)
        model = self._session.models[0] if self._session.models else "not configured"
        effort = self.state.effort if role is ModelRole.ORCHESTRATOR else None
        return ModelChoice(role=role, model_id=model, effort=effort)

    async def _ask_role(self, title: str) -> ModelRole | None:
        def label(name: str, what: str, role: ModelRole) -> str:
            c = self._current(role)
            effort = c.effort.value if c.effort else "default"
            return f"{name:<13}{what:<26}{c.model_id}  ({effort})"

        options = [
            (label("Orchestrator", "plans and delegates", ModelRole.ORCHESTRATOR), "orchestrator"),
            (label("Subagents", "do the focused work", ModelRole.SUBAGENT), "subagent"),
        ]
        picked = await self.push_screen_wait(PickerScreen(title, options))
        return ModelRole(picked) if picked else None

    async def _pick_model(self, role: ModelRole | None) -> None:
        role = role or await self._ask_role("Change the model for")
        if role is None:
            return
        tuning = self._application.tuning
        self._notify_hint("Loading models…", seconds=10)
        models = await tuning.available_models() if tuning else self._session.models
        self._hint_notice = None
        if not models:
            self._emit_output("/model", "No models to choose from. Set MINDOS_MODEL_CHOICES.")
            return
        picked = await self.push_screen_wait(
            PickerScreen(
                f"Model for the {role.value}",
                [(m, m) for m in models],
                current=self._current(role).model_id,
            )
        )
        if picked:
            self._choose(role, model_id=picked, command="/model")

    async def _pick_effort(self, role: ModelRole | None) -> None:
        role = role or await self._ask_role("Change the reasoning effort for")
        if role is None:
            return
        tuning = self._application.tuning
        model = self._current(role).model_id
        levels = tuning.effort_levels(model) if tuning else self._session.effort_levels
        if not levels:
            self._emit_output("/think", f"{model} has no configurable reasoning.")
            return
        notes = {
            ReasoningEffort.MINIMAL: "fastest, almost no thinking",
            ReasoningEffort.LOW: "quick, light thinking",
            ReasoningEffort.MEDIUM: "balanced",
            ReasoningEffort.HIGH: "careful, slower",
            ReasoningEffort.XHIGH: "deepest, slowest",
        }
        options = [(f"{e.value:<8}{notes.get(e, '')}", e.value) for e in levels]
        current = self._current(role).effort
        picked = await self.push_screen_wait(
            PickerScreen(
                f"Reasoning effort for the {role.value} ({model})",
                options,
                current=current.value if current else None,
            )
        )
        if picked:
            self._choose(role, effort=ReasoningEffort(picked), command="/think")

    def _choose(
        self,
        role: ModelRole,
        *,
        model_id: str | None = None,
        effort: ReasoningEffort | None = None,
        command: str,
    ) -> None:
        """Apply a choice to the running session and say what it is now."""
        tuning = self._application.tuning
        try:
            if tuning is not None:
                choice = tuning.select(role, model_id=model_id, effort=effort)
            elif role is ModelRole.ORCHESTRATOR and effort is not None and model_id is None:
                if effort not in self._session.effort_levels:
                    raise ValueError(f"This model does not support '{effort.value}'.")
                choice = ModelChoice(
                    role=role, model_id=self._current(role).model_id, effort=effort
                )
            else:
                raise ValueError("That cannot be changed in this session.")
        except ValueError as exc:
            self._emit_output(command, str(exc))
            return
        if role is ModelRole.ORCHESTRATOR and choice.effort is not None:
            effort_now = choice.effort
            self._set_state(lambda s: viewmodel.set_effort(s, effort_now))
        shown = choice.effort.value if choice.effort else "default"
        self._emit_output(
            command, f"The {role.value} now uses {choice.model_id} with {shown} reasoning."
        )

    def _status_text(self) -> str:
        s = self.state

        def line(role: ModelRole) -> str:
            c = self._current(role)
            return f"{role.value:<12} {c.model_id} ({c.effort.value if c.effort else 'default'})"

        lines = [
            f"version {self._session.version}",
            f"cwd {self._session.cwd}",
            line(ModelRole.ORCHESTRATOR),
            line(ModelRole.SUBAGENT),
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
