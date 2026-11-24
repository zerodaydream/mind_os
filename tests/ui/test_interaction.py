"""Mouse and keyboard: select and copy text, scroll, and pick the model and reasoning effort."""

from __future__ import annotations

from dataclasses import replace

from textual.events import MouseScrollUp

from mindos.agents.fake import FakeRuntime
from mindos.application import Application, EventBus
from mindos.domain import ModelChoice, ModelRole, ReasoningEffort
from mindos.infra import SystemClock, new_id
from mindos.presentation import MindOSApp
from mindos.presentation.picker import PickerScreen
from mindos.presentation.viewmodel import UserRow
from tests.ui.test_tui import SIZE, info, make_app, send

ORCH, SUB = ModelRole.ORCHESTRATOR, ModelRole.SUBAGENT
LOW, MEDIUM, HIGH = ReasoningEffort.LOW, ReasoningEffort.MEDIUM, ReasoningEffort.HIGH


def fill(app: MindOSApp, count: int = 120) -> None:
    rows = tuple(UserRow(id=f"u{i}", text=f"line number {i} lorem ipsum") for i in range(count))
    app.state = replace(app.state, rows=rows)
    app._needs_apply = True


class Clipboard:
    def __init__(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        self.copied: list[str] = []
        monkeypatch.setattr(
            "mindos.presentation.app.copy_text", lambda _app, t: self.copied.append(t)
        )


# ---- select and copy -------------------------------------------------------------------------
async def test_dragging_over_the_transcript_selects_and_releasing_copies(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clip = Clipboard(monkeypatch)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app)
        await pilot.pause(0.2)
        t = app.transcript
        await pilot.mouse_down(t, offset=(5, 3))
        await pilot.hover(t, offset=(30, 6))
        await pilot.mouse_up(t, offset=(30, 6))
        await pilot.pause()
        selected = app.screen.get_selected_text() or ""
        assert "line number" in selected and "\n" in selected
        assert clip.copied == [selected]  # copied on release, once


async def test_ctrl_c_copies_a_selection_instead_of_quitting_or_clearing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clip = Clipboard(monkeypatch)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app)
        app.prompt.set_text("keep me")
        await pilot.pause(0.2)
        t = app.transcript
        await pilot.mouse_down(t, offset=(2, 2))
        await pilot.hover(t, offset=(20, 4))
        await pilot.mouse_up(t, offset=(20, 4))
        clip.copied.clear()  # forget the copy made on release
        await pilot.press("ctrl+c")
        assert len(clip.copied) == 1 and clip.copied[0]
        assert app.prompt.text == "keep me"  # Ctrl+C did its copy job only
        assert app._exit_armed_until == 0.0


async def test_ctrl_c_with_nothing_selected_keeps_its_old_meaning(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clip = Clipboard(monkeypatch)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        app.prompt.set_text("draft")
        await pilot.press("ctrl+c")
        assert app.prompt.text == "" and clip.copied == []


async def test_selected_text_in_the_prompt_copies_too(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clip = Clipboard(monkeypatch)
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        app.prompt.set_text("hello world")
        app.prompt.select_all()
        await pilot.press("ctrl+c")
        assert clip.copied == ["hello world"]


# ---- scrolling -------------------------------------------------------------------------------
async def test_wheel_pages_and_jump_keys_scroll_the_transcript() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        fill(app)
        await pilot.pause(0.2)
        t = app.transcript
        tail = t.scroll_y
        t.post_message(MouseScrollUp(t, 5, 5, 0, 0, 0, False, False, False))
        await pilot.pause()
        assert t.scroll_y < tail  # the trackpad wheel moves it
        await pilot.press("ctrl+home")
        await pilot.pause()
        assert t.scroll_y == 0
        await pilot.press("alt+down")
        await pilot.pause()
        assert t.scroll_y == 3
        await pilot.press("alt+up")
        await pilot.pause()
        assert t.scroll_y == 0
        await pilot.press("ctrl+end")
        await pilot.pause(0.1)
        assert t.at_tail


# ---- choosing a model and reasoning effort ---------------------------------------------------
class Tuning:
    def __init__(self) -> None:
        self.chosen = {ORCH: ModelChoice(role=ORCH, model_id="m-big", effort=HIGH)}
        self.chosen[SUB] = ModelChoice(role=SUB, model_id="m-small", effort=LOW)
        self.calls: list[tuple[ModelRole, str | None, ReasoningEffort | None]] = []

    def current(self, role: ModelRole) -> ModelChoice:
        return self.chosen[role]

    async def available_models(self) -> tuple[str, ...]:
        return ("m-big", "m-small", "m-new")

    def effort_levels(self, model_id: str) -> tuple[ReasoningEffort, ...]:
        return (LOW, MEDIUM, HIGH)

    def select(
        self,
        role: ModelRole,
        *,
        model_id: str | None = None,
        effort: ReasoningEffort | None = None,
    ) -> ModelChoice:
        self.calls.append((role, model_id, effort))
        old = self.chosen[role]
        self.chosen[role] = ModelChoice(
            role=role, model_id=model_id or old.model_id, effort=effort or old.effort
        )
        return self.chosen[role]


def tuned_app() -> tuple[MindOSApp, Tuning]:
    tuning = Tuning()
    application = Application(
        runtime=FakeRuntime(speed=60),
        bus=EventBus(SystemClock()),
        new_id=new_id,
        tuning=tuning,
    )
    return MindOSApp(application, info()), tuning


def options(app: MindOSApp) -> list[str]:
    screen = app.screen
    assert isinstance(screen, PickerScreen)
    lst = screen.query_one("#picker-list")
    return [str(lst.get_option_at_index(i).prompt) for i in range(lst.option_count)]  # type: ignore[attr-defined]


async def test_model_picker_asks_which_agent_then_lists_models_and_applies_the_choice() -> None:
    app, tuning = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/model")
        await pilot.pause(0.2)
        roles = options(app)
        assert "Orchestrator" in roles[0] and "m-big" in roles[0]
        assert "Subagents" in roles[1] and "m-small" in roles[1]
        await pilot.press("down", "enter")  # subagents
        await pilot.pause(0.2)
        models = options(app)
        assert [m.strip(" ●") for m in models] == ["m-big", "m-small", "m-new"]
        assert models[1].startswith("●")  # the current one is marked
        await pilot.press("down", "enter")  # m-new
        await pilot.pause(0.2)
        assert tuning.calls == [(SUB, "m-new", None)]
        assert not isinstance(app.screen, PickerScreen)
        text = " ".join(getattr(r, "text", "") for r in app.state.rows)
        assert "subagent now uses m-new" in text


async def test_effort_picker_sets_the_subagent_level_independently() -> None:
    app, tuning = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/think")
        await pilot.pause(0.2)
        await pilot.press("down", "enter")  # subagents
        await pilot.pause(0.2)
        levels = options(app)
        assert [x.split()[0].strip("●") or x.split()[1] for x in levels][:3] == [
            "low",
            "medium",
            "high",
        ]
        await pilot.press("down", "enter")  # medium (low is current and highlighted first)
        await pilot.pause(0.2)
        assert tuning.calls == [(SUB, None, MEDIUM)]
        assert app.state.effort is HIGH  # the orchestrator's effort did not move


async def test_choosing_the_orchestrator_effort_updates_the_status_line_state() -> None:
    app, tuning = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/think")
        await pilot.pause(0.2)
        await pilot.press("enter")  # orchestrator is first
        await pilot.pause(0.2)
        await pilot.press("up", "enter")  # medium: one above the highlighted current (high)
        await pilot.pause(0.2)
        assert tuning.calls == [(ORCH, None, MEDIUM)] and app.state.effort is MEDIUM


async def test_escape_cancels_a_picker_without_changing_anything() -> None:
    app, tuning = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/model")
        await pilot.pause(0.2)
        assert isinstance(app.screen, PickerScreen)
        await pilot.press("escape")
        await pilot.pause(0.2)
        assert not isinstance(app.screen, PickerScreen) and tuning.calls == []


async def test_typed_forms_skip_the_lists() -> None:
    app, tuning = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/think subagent medium")
        await send(app, pilot, "/model subagent m-new")
        await send(app, pilot, "/think low")
        await pilot.pause(0.2)
        assert tuning.calls == [(SUB, None, MEDIUM), (SUB, "m-new", None), (ORCH, None, LOW)]
        assert not isinstance(app.screen, PickerScreen)


async def test_status_lists_both_agents() -> None:
    app, _ = tuned_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/status")
        await pilot.pause(0.2)
        text = " ".join(getattr(r, "text", "") for r in app.state.rows)
        assert "orchestrator m-big (high)" in text and "subagent     m-small (low)" in text


async def test_without_a_tuner_the_orchestrator_effort_still_works_and_models_say_so() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/think low")
        await send(app, pilot, "/model subagent x")
        await pilot.pause(0.2)
        text = " ".join(getattr(r, "text", "") for r in app.state.rows)
        assert "with low reasoning" in text and "cannot be changed in this session" in text
        assert app.state.effort is LOW
