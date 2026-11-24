"""Orchestrator-level internal tools: `write_todos` and `read_artifact` (risk R0).

`delegate` is also an orchestrator tool but needs the subagent runner, so `agents/` builds it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mindos.domain import ArtifactStore, events
from mindos.tools.spec import ToolContext, ToolError, ToolSpec

MAX_SLICE = 4000  # stays under the default result bound, so a slice is never offloaded again


class TodoItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    status: Literal["pending", "in_progress", "done"] = "pending"


class WriteTodosInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    todos: list[TodoItem] = Field(
        max_length=30, description="The complete list. It replaces the previous list."
    )


async def _write_todos(args: WriteTodosInput, ctx: ToolContext) -> str:
    ctx.emit(
        events.PlanUpdated(
            run_id=ctx.run_id,
            todos=tuple(events.Todo(id=t.id, text=t.text, status=t.status) for t in args.todos),
        )
    )
    return f"Plan updated: {len(args.todos)} item(s)."


WRITE_TODOS = ToolSpec(
    name="write_todos",
    version="1",
    summary=(
        "Record or update the task plan shown to the user. Send the full list each time. "
        "Use it for work with three or more steps."
    ),
    input_model=WriteTodosInput,
    handler=_write_todos,
    timeout_s=5,
)


class ReadArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(pattern=r"^art_[A-Za-z0-9]{10,40}$")
    offset: int = Field(default=0, ge=0, description="Character offset to start from.")
    limit: int = Field(default=MAX_SLICE, ge=1, le=MAX_SLICE)


def read_artifact_spec(store: ArtifactStore) -> ToolSpec:
    async def handler(args: ReadArtifactInput, ctx: ToolContext) -> str:
        piece = await store.read(args.artifact_id, offset=args.offset, limit=args.limit)
        if piece is None:
            raise ToolError(f"No artifact '{args.artifact_id}'.", code="not_found")
        end = piece.offset + len(piece.text)
        more = f"next_offset={piece.next_offset}" if piece.next_offset is not None else "end"
        # Plain text, not JSON: escaping would push a full slice over the result bound.
        return f"{piece.text}\n[characters {piece.offset}-{end} of {piece.total}; {more}]"

    return ToolSpec(
        name="read_artifact",
        version="1",
        summary=(
            "Read part of a large tool result that was stored instead of shown. "
            "Pass the artifact_id from the truncation note and an offset."
        ),
        input_model=ReadArtifactInput,
        handler=handler,
        untrusted_output=True,  # artifacts may hold text from email, files, or the web
        timeout_s=10,
        max_result_chars=MAX_SLICE + 200,
    )
