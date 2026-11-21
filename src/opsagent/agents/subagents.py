"""Subagent definitions, the runner, and the `delegate` tool.

A subagent invocation is isolated: it gets only a `TaskBrief`, runs its own message history, and
returns only a `SubagentResult` built by code. Its thoughts and tool traffic never reach the
orchestrator. Depth is one: no subagent holds `delegate`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol
from zoneinfo import ZoneInfo

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from opsagent.agents.bridge import EngineMiddleware, stub_tools
from opsagent.agents.hub import RunHub, RunState
from opsagent.agents.messages import split_content, usage_of
from opsagent.agents.prompts import calendar_prompt
from opsagent.agents.usage import UsageTracker
from opsagent.domain import (
    Clock,
    IdFactory,
    ModelRole,
    ReasoningEffort,
    SubagentResult,
    SubagentStatus,
    TaskBrief,
    ToolIdempotency,
    bind,
    events,
    redact_text,
)
from opsagent.tools import ToolContext, ToolError, ToolExecutionEngine, ToolSpec

log = logging.getLogger(__name__)

SUMMARY_CHARS = 4000


class ChatModels(Protocol):
    """Builds tool-calling chat models. Implemented by the provider adapter."""

    def chat(self, role: ModelRole, effort: ReasoningEffort | None) -> BaseChatModel: ...

    def model_id(self, role: ModelRole) -> str: ...


@dataclass(frozen=True)
class SubagentDefinition:
    name: str
    description: str
    prompt: Callable[..., str]  # (now=..., tz=...) -> system prompt
    tools: frozenset[str]
    role: ModelRole = ModelRole.SUBAGENT


CALENDAR = SubagentDefinition(
    name="calendar",
    description="Reads the user's calendar: find events, check a day or week, get event details.",
    prompt=calendar_prompt,
    tools=frozenset({"calendar.search_events", "calendar.get_event"}),
)


def render_brief(brief: TaskBrief) -> str:
    lines = [f"Task: {brief.goal}"]
    if brief.constraints:
        lines += ["Constraints:", *(f"- {c}" for c in brief.constraints)]
    if brief.context:
        lines += ["Context from the orchestrator:", *(f"- {c}" for c in brief.context)]
    if brief.expected_result:
        lines.append(f"Expected result: {brief.expected_result}")
    return "\n".join(lines)


class SubagentRunner:
    def __init__(
        self,
        *,
        definitions: tuple[SubagentDefinition, ...],
        models: ChatModels,
        hub: RunHub,
        tracker: UsageTracker,
        clock: Clock,
        tz: ZoneInfo,
        new_id: IdFactory,
        efforts: dict[ModelRole, ReasoningEffort],
    ) -> None:
        self._defs = {d.name: d for d in definitions}
        self._models = models
        self._hub = hub
        self._tracker = tracker
        self._clock = clock
        self._tz = tz
        self._new_id = new_id
        self._efforts = efforts
        self._engine: ToolExecutionEngine | None = None
        self._graphs: dict[str, Any] = {}

    @property
    def definitions(self) -> tuple[SubagentDefinition, ...]:
        return tuple(self._defs.values())

    def bind_engine(self, engine: ToolExecutionEngine) -> None:
        """The engine holds the `delegate` tool that holds this runner, so it is attached last."""
        if self._engine is not None:
            raise RuntimeError("engine already bound")
        self._engine = engine

    def graph(self, name: str) -> Any:
        """The compiled agent for a subagent. Built once per definition."""
        if self._engine is None:
            raise RuntimeError("engine is not bound")
        if name not in self._graphs:
            definition = self._defs[name]
            engine = self._engine
            self._graphs[name] = create_agent(
                self._models.chat(definition.role, self._efforts.get(definition.role)),
                tools=stub_tools(engine, name),
                middleware=[
                    EngineMiddleware(
                        engine=engine,
                        hub=self._hub,
                        agent=name,
                        system_prompt=self._prompt_for(definition),
                    )
                ],
                # False: never inherit the orchestrator's checkpointer when run inside a tool.
                checkpointer=False,
                name=name,
            )
        return self._graphs[name]

    def _prompt_for(self, definition: SubagentDefinition) -> Callable[[], str]:
        def prompt() -> str:
            return definition.prompt(now=self._clock.now(), tz=self._tz)

        return prompt

    async def run(self, name: str, brief: TaskBrief, ctx: ToolContext) -> SubagentResult:
        state = self._hub.get(ctx.run_id)
        if state is None:
            raise ToolError("No active run.", code="internal_error")
        definition = self._defs.get(name)
        if definition is None:
            known = ", ".join(sorted(self._defs))
            raise ToolError(f"Unknown subagent '{name}'. Available: {known}.", code="not_found")
        async with state.slots:  # bounded parallelism
            subagent_id = self._new_id("sub")
            ctx.emit(
                events.SubagentStarted(
                    run_id=ctx.run_id,
                    subagent_id=subagent_id,
                    subagent=name,
                    brief=brief.goal[:200],
                )
            )
            with bind(subagent_id=subagent_id):
                result = await self._invoke(definition, brief, state, subagent_id)
            ctx.emit(
                events.SubagentFinished(
                    run_id=ctx.run_id,
                    subagent_id=subagent_id,
                    status=result.status,
                    summary=result.summary[:300],
                )
            )
        return result

    async def _invoke(
        self, definition: SubagentDefinition, brief: TaskBrief, state: RunState, subagent_id: str
    ) -> SubagentResult:
        ledger = state.ledger(subagent_id)
        messages: list[Any] = []
        status: SubagentStatus = "success"
        note = ""
        try:
            out = await self.graph(definition.name).ainvoke(
                {"messages": [HumanMessage(render_brief(brief))]},
                {"recursion_limit": state.limits.recursion(state.limits.subagent_steps)},
            )
            messages = out["messages"]
        except GraphRecursionError:
            status, note = "partial", "The subagent hit its step limit before finishing."
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("subagent %s failed", definition.name)
            status, note = "failed", f"The subagent failed: {type(exc).__name__}."
            ledger.errors.append(f"{definition.name}: {type(exc).__name__}")
        for message in messages:
            if isinstance(message, AIMessage):
                await self._tracker.record(
                    state,
                    role=definition.role,
                    model=self._models.model_id(definition.role),
                    usage=usage_of(message),
                    effort=self._efforts.get(definition.role),
                )
        text = _final_text(messages)
        if status == "success" and not text:
            status, note = "failed", "The subagent finished without an answer."
        if status == "success" and any(
            not e.endswith(": invalid_arguments") for e in ledger.errors
        ):
            status = (
                "partial"  # a tool failed and was not just a corrected typo; answer may be thin
            )
        summary = redact_text(text or note)[:SUMMARY_CHARS]
        if status == "partial" and note:
            summary = f"{note} {summary}".strip()
        return SubagentResult(
            status=status,
            summary=summary,
            evidence=tuple(ledger.evidence),
            artifacts=tuple(ledger.artifacts),
            errors=tuple(ledger.errors),
        )


def _final_text(messages: list[Any]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            return split_content(message.content)[0].strip()
    return ""


class DelegateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subagent: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    goal: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    constraints: list[Annotated[str, StringConstraints(max_length=300)]] = Field(
        default_factory=list, max_length=10
    )
    context: list[Annotated[str, StringConstraints(max_length=1000)]] = Field(
        default_factory=list, max_length=10
    )
    expected_result: Annotated[str, StringConstraints(max_length=300)] | None = None


def delegate_spec(runner: SubagentRunner, *, timeout_s: float) -> ToolSpec:
    async def handler(args: DelegateInput, ctx: ToolContext) -> SubagentResult:
        brief = TaskBrief(
            goal=args.goal,
            constraints=tuple(args.constraints),
            context=tuple(args.context),
            expected_result=args.expected_result,
        )
        return await runner.run(args.subagent, brief, ctx)

    names = ", ".join(d.name for d in runner.definitions)
    return ToolSpec(
        name="delegate",
        version="1",
        summary=(
            "Hand a self-contained task to a subagent and get back its result "
            f"(status, summary, evidence). Subagents: {names}. "
            "It cannot see this conversation: include every detail it needs."
        ),
        input_model=DelegateInput,
        handler=handler,
        # Phase 04 subagents are read-only. A side-effecting subagent must not be READ_ONLY.
        idempotency=ToolIdempotency.NON_IDEMPOTENT,
        timeout_s=timeout_s,
        untrusted_output=True,  # the summary is model text built from untrusted tool data
        max_result_chars=8000,
        evidence=lambda raw: raw.evidence,
    )
