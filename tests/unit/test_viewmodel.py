from __future__ import annotations

from opsagent.agents.fake import load_scenarios
from opsagent.domain import ApprovalMode, ReasoningEffort, RiskClass, events, parse_event
from opsagent.domain.events import ApprovalView, Todo
from opsagent.presentation import viewmodel as vm
from opsagent.presentation.viewmodel import (
    AssistantRow,
    NoticeRow,
    SessionInfo,
    SubagentRow,
    ThinkingRow,
    TodosRow,
    ToolRow,
    ViewState,
)

INFO = SessionInfo(version="0.0", cwd="~/x", models=("m",), effort=ReasoningEffort.HIGH)


def replay(name: str, run_id: str = "r1", *, upto_suspend: bool = True) -> ViewState:
    state = vm.initial_state(INFO)
    state = vm.reduce(
        state,
        events.RunStarted(run_id=run_id, conversation_id="c1", thread_id="t1", user_text="hi"),
    )
    for entry in load_scenarios()[name]:
        if entry.get("suspend"):
            if upto_suspend:
                break
            continue
        if entry.get("when"):
            continue
        state = vm.reduce(state, parse_event({**entry["event"], "run_id": run_id}))  # type: ignore[arg-type]
    return state


def test_initial_state_has_welcome_row() -> None:
    state = vm.initial_state(INFO)
    assert [r.id for r in state.rows] == ["welcome"]
    assert state.effort == ReasoningEffort.HIGH


def test_stream_scenario_builds_thinking_and_assistant_rows() -> None:
    state = replay("stream")
    thought = next(r for r in state.rows if isinstance(r, ThinkingRow))
    reply = next(r for r in state.rows if isinstance(r, AssistantRow))
    assert thought.done and thought.tokens == 214
    assert reply.done and reply.text.startswith("Here is a short plan")
    assert state.reasoning_tokens == 214
    assert state.cost_usd == 0.0021
    assert state.running  # no terminal event in the script


def test_deltas_accumulate_then_complete_replaces() -> None:
    state = vm.initial_state(INFO)
    for text in ("he", "llo"):
        state = vm.reduce(state, events.AssistantTextDelta(run_id="r", message_id="m", text=text))
    assert next(r for r in state.rows if isinstance(r, AssistantRow)).text == "hello"
    state = vm.reduce(
        state, events.AssistantMessageComplete(run_id="r", message_id="m", text="hello!")
    )
    rows = [r for r in state.rows if isinstance(r, AssistantRow)]
    assert len(rows) == 1 and rows[0].text == "hello!" and rows[0].done


def test_tools_scenario_updates_rows_in_place() -> None:
    state = replay("tools")
    tools = {r.id: r for r in state.rows if isinstance(r, ToolRow)}
    assert tools["t:t1"].status == "ok" and "Found 3 files" in tools["t:t1"].result
    assert tools["t:t3"].indent == 1  # subagent tool is nested
    agent = next(r for r in state.rows if isinstance(r, SubagentRow))
    assert agent.status == "success"
    todos = next(r for r in state.rows if isinstance(r, TodosRow))
    assert all(t.status == "done" for t in todos.todos)
    assert len([r for r in state.rows if isinstance(r, TodosRow)]) == 1


def test_approval_lifecycle() -> None:
    state = replay("approval")
    assert len(state.approvals) == 1 and state.waiting_for_approval
    approval_id = state.approvals[0].view.approval_id
    state = vm.reduce(
        state, events.ApprovalResolved(run_id="r1", approval_id=approval_id, outcome="approved")
    )
    assert not state.approvals


def test_terminal_events_end_the_run_and_drop_its_approvals() -> None:
    state = replay("approval")
    state = vm.reduce(state, events.RunCancelled(run_id="r1"))
    assert not state.running and not state.approvals
    assert isinstance(state.rows[-1], NoticeRow) and state.rows[-1].text == "Interrupted"


def test_error_scenario_ends_failed_with_notice_and_degraded_flag() -> None:
    state = replay("error")
    assert not state.running
    assert "google.calendar" in state.degraded
    assert any(isinstance(r, NoticeRow) and r.level == "error" for r in state.rows)
    state = vm.reduce(state, events.Recovered(component="google.calendar"))
    assert not state.degraded


def test_two_runs_track_independently() -> None:
    state = vm.initial_state(INFO)
    for rid in ("a", "b"):
        state = vm.reduce(
            state, events.RunStarted(run_id=rid, conversation_id="c", thread_id="t", user_text="")
        )
    state = vm.reduce(state, events.RunFinished(run_id="a"))
    assert state.running and state.active_runs == frozenset({"b"})
    state = vm.reduce(state, events.RunFinished(run_id="b"))
    assert not state.running


def test_usage_effort_mode_and_local_actions() -> None:
    state = vm.initial_state(INFO)
    state = vm.reduce(state, events.ModeChanged(mode=ApprovalMode.PLAN))
    assert state.mode == ApprovalMode.PLAN
    state = vm.set_effort(state, ReasoningEffort.LOW)
    assert state.effort == ReasoningEffort.LOW
    state = vm.toggle_expanded(vm.toggle_todos(state))
    assert state.expanded and not state.todos_visible
    state = vm.add_user_message(state, "hi")
    state = vm.clear_transcript(state, INFO)
    assert [r.id for r in state.rows] == ["welcome"]


def test_unknown_progress_for_missing_tool_is_ignored() -> None:
    state = vm.initial_state(INFO)
    again = vm.reduce(state, events.ToolCallProgress(run_id="r", tool_call_id="nope", note="x"))
    assert again is state


def test_reducer_never_mutates_previous_state() -> None:
    before = vm.initial_state(INFO)
    rows_before = before.rows
    vm.reduce(before, events.PlanUpdated(run_id="r", todos=(Todo(id="1", text="a"),)))
    assert before.rows is rows_before


def test_approval_view_round_trip_in_state() -> None:
    view = ApprovalView(
        approval_id="a1",
        tool="t",
        risk=RiskClass.R3,
        summary="s",
        payload_preview="{}",
        payload_hash="h",
    )
    state = vm.reduce(vm.initial_state(INFO), events.ApprovalRequested(run_id="r", approval=view))
    assert state.approvals[0].view == view


def test_a_replayed_approval_request_does_not_stack_a_second_dialog() -> None:
    view = ApprovalView(
        approval_id="apr_1",
        tool="t.x",
        risk=RiskClass.R3,
        summary="s",
        payload_preview="{}",
        payload_hash="h",
    )
    state = vm.initial_state(INFO)
    for _ in range(3):  # a resume, then a recovery after restart
        state = vm.reduce(state, events.ApprovalRequested(run_id="r", approval=view))
    assert [a.view.approval_id for a in state.approvals] == ["apr_1"]
