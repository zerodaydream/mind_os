"""Immutable view-model and the event reducer (ARCHITECTURE section 13.3).

`AppEvent -> reduce -> ViewState -> widgets`. This module has no Textual or Rich imports, so the
whole UI state machine is testable as plain functions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from opsagent.domain import AppEvent, ApprovalMode, ReasoningEffort, events
from opsagent.domain.events import ApprovalView, Todo

NoticeLevel = Literal["info", "warning", "error", "dim"]
ToolStatus = Literal["running", "ok", "error", "rejected"]
SubagentStatus = Literal["running", "success", "partial", "failed", "needs_input"]


# ---- session info (supplied once by the composition root) --------------------------------------
@dataclass(frozen=True, slots=True)
class SessionInfo:
    version: str
    cwd: str
    models: tuple[str, ...] = ()
    effort: ReasoningEffort | None = None
    effort_levels: tuple[ReasoningEffort, ...] = tuple(ReasoningEffort)
    tip: str = "Type a request, or / for commands. Press ? for shortcuts."
    plain: bool = False  # NO_COLOR or ASCII-only terminal: no color, ASCII glyphs, no motion


# ---- rows --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WelcomeRow:
    version: str
    cwd: str
    models: tuple[str, ...]
    tip: str
    id: str = "welcome"


@dataclass(frozen=True, slots=True)
class UserRow:
    id: str
    text: str


@dataclass(frozen=True, slots=True)
class AssistantRow:
    id: str
    text: str
    done: bool = False


@dataclass(frozen=True, slots=True)
class ThinkingRow:
    id: str
    text: str
    tokens: int | None = None
    done: bool = False


@dataclass(frozen=True, slots=True)
class ToolRow:
    id: str
    tool: str
    args: str
    status: ToolStatus = "running"
    result: str = ""
    duration_ms: int | None = None
    indent: int = 0


@dataclass(frozen=True, slots=True)
class SubagentRow:
    id: str
    name: str
    brief: str
    status: SubagentStatus = "running"
    summary: str = ""


@dataclass(frozen=True, slots=True)
class TodosRow:
    todos: tuple[Todo, ...]
    id: str = "todos"


@dataclass(frozen=True, slots=True)
class NoticeRow:
    id: str
    level: NoticeLevel
    text: str


@dataclass(frozen=True, slots=True)
class CommandRow:
    id: str
    command: str
    text: str


Row = (
    WelcomeRow
    | UserRow
    | AssistantRow
    | ThinkingRow
    | ToolRow
    | SubagentRow
    | TodosRow
    | NoticeRow
    | CommandRow
)


# ---- state -------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PendingApproval:
    run_id: str | None
    view: ApprovalView


@dataclass(frozen=True, slots=True)
class ViewState:
    rows: tuple[Row, ...]
    counter: int = 0
    active_runs: frozenset[str] = frozenset()
    run_started: datetime | None = None
    conversation_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    context_pct: float | None = None
    cost_usd: float | None = None
    effort: ReasoningEffort | None = None
    mode: ApprovalMode = ApprovalMode.DEFAULT
    approvals: tuple[PendingApproval, ...] = ()
    degraded: frozenset[str] = frozenset()
    expanded: bool = False
    todos_visible: bool = True

    @property
    def running(self) -> bool:
        return bool(self.active_runs)

    @property
    def waiting_for_approval(self) -> bool:
        return bool(self.approvals)


def initial_state(info: SessionInfo) -> ViewState:
    welcome = WelcomeRow(version=info.version, cwd=info.cwd, models=info.models, tip=info.tip)
    return ViewState(rows=(welcome,), effort=info.effort)


# ---- helpers -----------------------------------------------------------------------------------
def _find(rows: tuple[Row, ...], row_id: str) -> int:
    for i in range(len(rows) - 1, -1, -1):
        if rows[i].id == row_id:
            return i
    return -1


def _get[T: Row](rows: tuple[Row, ...], row_id: str, kind: type[T], default: T) -> T:
    row = rows[i] if (i := _find(rows, row_id)) >= 0 else None
    return row if isinstance(row, kind) else default


def _upsert(rows: tuple[Row, ...], row: Row) -> tuple[Row, ...]:
    i = _find(rows, row.id)
    if i < 0:
        return (*rows, row)
    return (*rows[:i], row, *rows[i + 1 :])


def _add_notice(state: ViewState, level: NoticeLevel, text: str) -> ViewState:
    row = NoticeRow(id=f"n{state.counter}", level=level, text=text)
    return replace(state, rows=(*state.rows, row), counter=state.counter + 1)


# ---- local (UI-originated) actions -------------------------------------------------------------
def add_user_message(state: ViewState, text: str) -> ViewState:
    row = UserRow(id=f"u{state.counter}", text=text)
    return replace(state, rows=(*state.rows, row), counter=state.counter + 1)


def add_notice(state: ViewState, level: NoticeLevel, text: str) -> ViewState:
    return _add_notice(state, level, text)


def add_command_output(state: ViewState, command: str, text: str) -> ViewState:
    row = CommandRow(id=f"c{state.counter}", command=command, text=text)
    return replace(state, rows=(*state.rows, row), counter=state.counter + 1)


def toggle_expanded(state: ViewState) -> ViewState:
    return replace(state, expanded=not state.expanded)


def toggle_todos(state: ViewState) -> ViewState:
    return replace(state, todos_visible=not state.todos_visible)


def set_effort(state: ViewState, effort: ReasoningEffort) -> ViewState:
    return replace(state, effort=effort)


def clear_transcript(state: ViewState, info: SessionInfo) -> ViewState:
    return replace(state, rows=initial_state(info).rows, counter=0)


# ---- reducer -----------------------------------------------------------------------------------
def reduce(state: ViewState, event: AppEvent) -> ViewState:  # noqa: PLR0911, PLR0912, PLR0915
    """Apply one application event. Pure: returns a new state."""
    row: Row
    match event:
        case events.RunStarted():
            fresh = not state.active_runs
            return replace(
                state,
                active_runs=state.active_runs | {event.run_id or ""},
                run_started=event.ts if fresh else state.run_started,
                conversation_id=event.conversation_id,
                output_tokens=0 if fresh else state.output_tokens,
                reasoning_tokens=0 if fresh else state.reasoning_tokens,
            )
        case events.RunFinished():
            return _end_run(state, event.run_id)
        case events.RunFailed():
            ended = _end_run(state, event.run_id)
            return _add_notice(ended, "error", f"Run failed ({event.code}): {event.message}")
        case events.RunCancelled():
            ended = _end_run(state, event.run_id)
            return _add_notice(ended, "dim", "Interrupted")
        case events.AssistantTextDelta():
            row_id = f"a:{event.message_id}"
            reply = _get(state.rows, row_id, AssistantRow, AssistantRow(id=row_id, text=""))
            row = replace(reply, text=reply.text + event.text)
            return replace(state, rows=_upsert(state.rows, row))
        case events.AssistantMessageComplete():
            row = AssistantRow(id=f"a:{event.message_id}", text=event.text, done=True)
            return replace(state, rows=_upsert(state.rows, row))
        case events.ThinkingDelta():
            row_id = f"th:{event.message_id}"
            thought = _get(state.rows, row_id, ThinkingRow, ThinkingRow(id=row_id, text=""))
            row = replace(thought, text=thought.text + event.text)
            return replace(state, rows=_upsert(state.rows, row))
        case events.ThinkingComplete():
            row_id = f"th:{event.message_id}"
            thought = _get(state.rows, row_id, ThinkingRow, ThinkingRow(id=row_id, text=""))
            row = replace(thought, tokens=event.tokens, done=True)
            return replace(state, rows=_upsert(state.rows, row))
        case events.PlanUpdated():
            return replace(state, rows=_upsert(state.rows, TodosRow(todos=event.todos)))
        case events.SubagentStarted():
            row = SubagentRow(id=f"sa:{event.subagent_id}", name=event.subagent, brief=event.brief)
            return replace(state, rows=_upsert(state.rows, row))
        case events.SubagentFinished():
            row_id = f"sa:{event.subagent_id}"
            blank_agent = SubagentRow(id=row_id, name="subagent", brief="")
            agent = _get(state.rows, row_id, SubagentRow, blank_agent)
            row = replace(agent, status=event.status, summary=event.summary)
            return replace(state, rows=_upsert(state.rows, row))
        case events.ToolCallStarted():
            row = ToolRow(
                id=f"t:{event.tool_call_id}",
                tool=event.tool,
                args=event.args_preview,
                indent=1 if event.subagent_id else 0,
            )
            return replace(state, rows=_upsert(state.rows, row))
        case events.ToolCallProgress():
            row_id = f"t:{event.tool_call_id}"
            if _find(state.rows, row_id) < 0:
                return state
            empty = ToolRow(id=row_id, tool="", args="")
            running = replace(_get(state.rows, row_id, ToolRow, empty), result=event.note)
            return replace(state, rows=_upsert(state.rows, running))
        case events.ToolCallFinished():
            row_id = f"t:{event.tool_call_id}"
            blank_tool = ToolRow(id=row_id, tool=event.tool, args="")
            tool = _get(state.rows, row_id, ToolRow, blank_tool)
            row = replace(
                tool,
                status=event.status,
                result=event.result_preview or "",
                duration_ms=event.duration_ms,
            )
            return replace(state, rows=_upsert(state.rows, row))
        case events.ApprovalRequested():
            pending = PendingApproval(run_id=event.run_id, view=event.approval)
            return replace(state, approvals=(*state.approvals, pending))
        case events.ApprovalResolved():
            return _drop_approval(state, event.approval_id)
        case events.ApprovalExpired():
            dropped = _drop_approval(state, event.approval_id)
            return _add_notice(dropped, "warning", "An approval request expired.")
        case events.AuthRequired():
            scopes = f" (new access: {', '.join(event.scope_delta)})" if event.scope_delta else ""
            return _add_notice(state, "warning", f"Sign-in needed for {event.provider}{scopes}.")
        case events.AuthRestored():
            return _add_notice(state, "info", f"{event.provider} is connected again.")
        case events.Notification():
            return _add_notice(state, event.level, event.text)
        case events.Degraded():
            noted = _add_notice(state, "warning", f"{event.component} degraded: {event.detail}")
            return replace(noted, degraded=state.degraded | {event.component})
        case events.Recovered():
            noted = _add_notice(state, "info", f"{event.component} recovered.")
            return replace(noted, degraded=state.degraded - {event.component})
        case events.ErrorEvent():
            hint = f" {event.remediation}" if event.remediation else ""
            return _add_notice(state, "error", f"{event.message}{hint}")
        case events.UsageUpdated():
            return replace(
                state,
                input_tokens=event.input_tokens or state.input_tokens,
                output_tokens=event.output_tokens or state.output_tokens,
                reasoning_tokens=event.reasoning_tokens or state.reasoning_tokens,
                context_pct=event.context_pct
                if event.context_pct is not None
                else state.context_pct,
                cost_usd=event.cost_usd if event.cost_usd is not None else state.cost_usd,
                effort=event.reasoning_effort or state.effort,
            )
        case events.ModeChanged():
            return replace(state, mode=event.mode)
        case events.CommandOutput():
            return add_command_output(state, event.command, event.text)
        case events.Rewound():
            return _add_notice(state, "dim", "Conversation rewound.")
        case _:
            return state  # JobEvent and future events: no transcript row yet


def _end_run(state: ViewState, run_id: str | None) -> ViewState:
    remaining = state.active_runs - {run_id or ""}
    kept = tuple(a for a in state.approvals if a.run_id != run_id)
    return replace(
        state,
        active_runs=remaining,
        approvals=kept,
        run_started=state.run_started if remaining else None,
    )


def _drop_approval(state: ViewState, approval_id: str) -> ViewState:
    kept = tuple(a for a in state.approvals if a.view.approval_id != approval_id)
    return replace(state, approvals=kept)
