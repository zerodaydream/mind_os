from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import replace

from textual.pilot import Pilot

from mindos.agents import UnavailableRuntime
from mindos.agents.fake import FakeRuntime
from mindos.application import Application, EventBus
from mindos.bootstrap import run_tui
from mindos.domain import ReasoningEffort
from mindos.infra import Settings, SystemClock, new_id
from mindos.presentation import MindOSApp, SessionInfo
from mindos.presentation.approval import ApprovalDialog
from mindos.presentation.render import RowRenderer
from mindos.presentation.transcript import Transcript
from mindos.presentation.viewmodel import (
    AssistantRow,
    CommandRow,
    NoticeRow,
    ToolRow,
    UserRow,
    initial_state,
)

SIZE = (110, 36)


def info(**kw: object) -> SessionInfo:
    base: dict[str, object] = {
        "version": "0.0",
        "cwd": "~/proj",
        "models": ("test-model",),
        "effort": ReasoningEffort.HIGH,
        "effort_levels": (ReasoningEffort.LOW, ReasoningEffort.HIGH),
    }
    return SessionInfo(**{**base, **kw})  # type: ignore[arg-type]


def make_app(**kw: object) -> MindOSApp:
    bus = EventBus(SystemClock())
    application = Application(runtime=FakeRuntime(speed=60), bus=bus, new_id=new_id)
    return MindOSApp(application, info(**kw))


async def until(pilot: Pilot, cond: Callable[[], bool], within: float = 8.0) -> None:
    deadline = time.monotonic() + within
    while not cond():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.03)


async def send(app: MindOSApp, pilot: Pilot, text: str) -> None:
    app.prompt.set_text(text)
    await pilot.press("enter")


def screen_text(app: MindOSApp) -> str:
    """Visible transcript lines as plain text."""
    t = app.transcript
    rows = []
    for y in range(t.size.height):
        strip = t.render_line(y)
        rows.append("".join(seg.text for seg in strip).rstrip())
    return "\n".join(rows)


def all_text(app: MindOSApp) -> str:
    t = app.transcript
    return "\n".join(
        "".join(s.text for s in strip).rstrip() for lines in t._lines for strip in lines
    )


# ---- rendering ----------------------------------------------------------------------------
def test_renderer_rows_and_collapse() -> None:
    r = RowRenderer(80)
    user = r.render(UserRow(id="u", text="hello there"), expanded=False, todos_visible=True)
    assert "".join(s.text for s in user[0]).startswith("> hello there")
    long = ToolRow(
        id="t",
        tool="files.search",
        args="q",
        status="ok",
        result="\n".join(f"l{i}" for i in range(10)),
    )
    collapsed = r.render(long, expanded=False, todos_visible=True)
    expanded = r.render(long, expanded=True, todos_visible=True)
    text = "\n".join("".join(s.text for s in line) for line in collapsed)
    assert "+7 lines (ctrl+o to expand)" in text
    assert len(expanded) == 1 + 10 and len(collapsed) == 1 + 4


def test_renderer_wraps_to_width_and_plain_mode_is_ascii() -> None:
    r = RowRenderer(40, plain=True)
    lines = r.render(
        AssistantRow(id="a", text="word " * 40, done=True), expanded=False, todos_visible=True
    )
    for strip in lines:
        assert strip.cell_length == 40
    assert all(ord(c) < 128 for line in lines for s in line for c in s.text)


# ---- performance gate ---------------------------------------------------------------------
async def test_50k_line_transcript_streaming_flush_under_50ms() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        t = app.transcript
        body = "line one\nline two\nline three"
        rows = tuple(
            ToolRow(id=f"t{i}", tool="files.read", args="x", status="ok", result=body)
            for i in range(12_500)
        )
        stream = AssistantRow(id="s", text="")
        app.state = replace(app.state, rows=(*rows, stream), expanded=True)
        app._needs_apply = True
        await pilot.pause(0.5)
        assert t.total_lines >= 50_000
        samples = []
        text = ""
        for _ in range(120):
            text += "streamed words "
            app.state = replace(app.state, rows=(*rows, AssistantRow(id="s", text=text)))
            start = time.perf_counter()
            app._apply_state()  # the full flush path: transcript, status line, hint, approvals
            samples.append(time.perf_counter() - start)
        samples.sort()
        p95 = samples[int(len(samples) * 0.95)] * 1000
        assert p95 < 50, f"flush p95 {p95:.1f} ms"


# ---- app scenarios ------------------------------------------------------------------------
async def test_stream_scenario_renders_markdown_and_status() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "show me a plan")
        await until(pilot, lambda: not app.state.running and app.state.cost_usd is not None)
        await pilot.pause(0.1)
        text = all_text(app)
        assert "> show me a plan" in text
        assert "Here is a short plan" in text and "Priorities" in text
        assert "def next_step" in text
        assert "Thought (214 tokens)" in text
        status = str(app.query_one("#status").render())
        assert "test-model" in status and "think high" in status and "$0.0021" in status


async def test_tools_scenario_shows_nested_rows_and_plan() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "tools")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        text = all_text(app)
        assert "files.search(" in text and "calendar.list_events(" in text
        assert "Subagent researcher" in text
        assert "Plan (3/3 done)" in text
        assert "Found 3 files" in text and "(ctrl+o to expand)" in text


async def test_ctrl_o_expands_and_ctrl_t_hides_plan() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "tools")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        assert "~/Downloads/budget-2026-v2.pdf" not in all_text(app)
        await pilot.press("ctrl+o")
        await pilot.pause(0.1)
        assert "~/Downloads/budget-2026-v2.pdf" in all_text(app)
        await pilot.press("ctrl+t")
        await pilot.pause(0.1)
        assert "Plan (" not in all_text(app)


async def test_error_scenario_shows_failure() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "error")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        text = all_text(app)
        assert "Calendar returned 503" in text
        assert "Run failed (ProviderUnavailable)" in text
        assert "degraded" in str(app.query_one("#status").render())


async def test_long_scenario_collapses_160_line_result() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "long")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        text = all_text(app)
        assert "+157 lines (ctrl+o to expand)" in text
        assert "report-000.txt" in text and "report-100.txt" not in text


async def test_approval_dialog_focuses_safe_option_and_approves() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "approval")
        await until(pilot, lambda: app.state.waiting_for_approval)
        await until(pilot, lambda: app.query(ApprovalDialog).first(None) is not None)
        dialog = app.query_one(ApprovalDialog)
        assert dialog._selected == 2  # focus starts on "No"
        assert app.prompt.disabled
        # the spinner repaints on a 0.1 s tick, so wait for it instead of racing it
        await until(pilot, lambda: "waiting" in str(app.query_one("#spinner").render()).lower())
        await pilot.press("a")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        assert "Done. The email is on its way." in all_text(app)
        assert not app.query(ApprovalDialog) and not app.prompt.disabled


async def test_approval_escape_declines() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "approval")
        await until(pilot, lambda: app.query(ApprovalDialog).first(None) is not None)
        await pilot.press("escape")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        text = all_text(app)
        assert "I did not send anything." in text and "You declined this action" in text


async def test_approval_edit_sends_edited_arguments() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "approval")
        await until(pilot, lambda: app.query(ApprovalDialog).first(None) is not None)
        await pilot.press("e")
        await pilot.pause(0.1)
        await pilot.press("enter")  # payload preview is valid JSON, send unchanged
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        assert "I sent the edited version." in all_text(app)


async def test_approval_reject_with_comment_flow() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "approval")
        await until(pilot, lambda: app.query(ApprovalDialog).first(None) is not None)
        await pilot.press("r")
        await pilot.pause(0.1)
        await pilot.press(*"use work email", "enter")
        await until(pilot, lambda: not app.state.running)
        assert "I did not send anything." in all_text(app)


# ---- keys and commands --------------------------------------------------------------------
async def test_escape_interrupts_a_running_run() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        app._application._runtime._speed = 0.5  # slow the script so we can interrupt it
        await send(app, pilot, "tools")
        await until(pilot, lambda: app.state.running)
        await pilot.press("escape")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        assert any(isinstance(r, NoticeRow) and r.text == "Interrupted" for r in app.state.rows)


async def test_multiline_input_and_history() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(*"line one", "ctrl+j", *"line two")
        assert app.prompt.text == "line one\nline two"
        app.prompt.clear_input()
        await pilot.press(*"first\\", "enter")
        await pilot.press(*"second")
        assert app.prompt.text == "first\nsecond"
        app.prompt.clear_input()
        await send(app, pilot, "/status")
        await send(app, pilot, "/cost")
        await pilot.press("up")
        assert app.prompt.text == "/cost"
        await pilot.press("up")
        assert app.prompt.text == "/status"
        await pilot.press("down", "down")
        assert app.prompt.text == ""


async def test_slash_commands() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "/help")
        await send(app, pilot, "/think low")
        await send(app, pilot, "/think xhigh")
        await send(app, pilot, "/nope")
        await send(app, pilot, "/memory")
        await pilot.pause(0.1)
        text = " ".join(r.text for r in app.state.rows if isinstance(r, CommandRow))
        assert "/clear" in text and "Ctrl+O" in text
        assert "with low reasoning" in text and "does not support 'xhigh'" in text
        assert "Unknown command /nope" in text and "/memory is not available yet" in text
        assert app.state.effort == ReasoningEffort.LOW
        await send(app, pilot, "/clear")
        await pilot.pause(0.1)
        assert [r.id for r in app.state.rows] == ["welcome"]


async def test_tab_completes_slash_command_and_hint_lists_matches() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("/", "t", "h")
        await pilot.pause(0.1)
        assert "/think" in str(app.query_one("#hint").render())
        await pilot.press("tab")
        assert app.prompt.text == "/think "


async def test_shift_tab_cycles_mode_and_alt_t_cycles_effort() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("shift+tab")
        await until(pilot, lambda: app.state.mode.value == "plan")
        assert "plan mode on" in str(app.query_one("#hint").render())
        await pilot.press("alt+t")
        await pilot.pause(0.1)
        assert app.state.effort == ReasoningEffort.LOW  # HIGH wraps to LOW in this info


async def test_double_ctrl_c_quits_and_ctrl_c_clears_input() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press(*"draft")
        await pilot.press("ctrl+c")
        assert app.prompt.text == ""
        await pilot.press("ctrl+c")
        assert "again to quit" in str(app.query_one("#hint").render())
        await pilot.press("ctrl+c")
        await pilot.pause(0.2)
    assert not app.is_running


async def test_question_mark_opens_shortcuts_and_stash_round_trips() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.press("question_mark")
        await pilot.pause(0.1)
        assert any(isinstance(r, CommandRow) and "Ctrl+S" in r.text for r in app.state.rows)
        await pilot.press(*"keep me")
        await pilot.press("ctrl+s")
        assert app.prompt.text == ""
        await pilot.press("ctrl+s")
        assert app.prompt.text == "keep me"


async def test_sticky_tail_and_jump_cue() -> None:
    app = make_app()
    async with app.run_test(size=(110, 20)) as pilot:
        await send(app, pilot, "long")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        assert app.transcript.at_tail
        await pilot.press("pageup")
        await pilot.pause(0.1)
        assert not app.transcript.at_tail
        assert "new output" in str(app.query_one("#hint").render())
        for _ in range(50):
            await pilot.press("pagedown")
        assert app.transcript.at_tail


async def test_resize_reflows_without_losing_rows() -> None:
    app = make_app()
    async with app.run_test(size=(120, 30)) as pilot:
        await send(app, pilot, "stream")
        await until(pilot, lambda: not app.state.running)
        await pilot.pause(0.1)
        wide = app.transcript.total_lines
        await pilot.resize_terminal(50, 30)
        await pilot.pause(0.2)
        assert app.transcript.total_lines > wide
        assert "Here is a short plan" in all_text(app)


async def test_initial_screen_has_welcome_block() -> None:
    app = make_app()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.1)
        text = screen_text(app)
        assert "MindOS" in text and "~/proj" in text
        assert "? for shortcuts" in str(app.query_one("#hint").render())
        assert initial_state(info()).rows[0].id == "welcome"


async def test_unavailable_runtime_shows_a_clear_failure() -> None:
    bus = EventBus(SystemClock())
    application = Application(runtime=UnavailableRuntime(), bus=bus, new_id=new_id)
    app = MindOSApp(application, info())
    async with app.run_test(size=SIZE) as pilot:
        await send(app, pilot, "hello")
        await until(pilot, lambda: any(isinstance(r, NoticeRow) for r in app.state.rows))
        await asyncio.sleep(0.05)
        assert "not connected" in all_text(app)


def test_transcript_is_a_scrollview_subclass() -> None:
    assert issubclass(
        Transcript, __import__("textual.scroll_view", fromlist=["ScrollView"]).ScrollView
    )


def test_run_tui_wires_demo_runtime_and_session_info(
    tmp_path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setattr(MindOSApp, "run", lambda self, **_: seen.update(app=self))
    settings = Settings(_env_file=None, log_dir=tmp_path, model_orchestrator="gem-x")
    run_tui(settings, demo=True)
    app = seen["app"]
    assert isinstance(app, MindOSApp)
    assert app._session.models == ("gem-x",)
    assert isinstance(app._application._runtime, FakeRuntime)
    assert len(app._session.effort_levels) == 5  # no key yet: every level offered
