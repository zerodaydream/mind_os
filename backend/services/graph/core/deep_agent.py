"""Deep Agents (LangGraph) for plan + execute intents."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader import get_config
from contextlib import nullcontext

from backend.common.langsmith_setup import (
    flush_traces,
    langsmith_run_config,
    trace_tool,
    tracing_active,
    turn_trace_id,
    unique_run_name,
)
from backend.common.database.models import Commitment
from backend.common.utils.enums import LlmMode
from backend.services.agents.core.markdown_tables import normalize_markdown_tables
from backend.services.agents.core.partial import emit_partial
from backend.services.agents.core.prompt_budget import DEEP_USER_CONTEXT_MAX, clip_text
from backend.services.agents.core.turn_context import preflight_prompt_block
from backend.services.integrations.core.calendar_events import create_calendar_event
from backend.services.integrations.core.url_shorten import shorten_url
from backend.services.integrations.core.gmail_sync import sync_gmail_inbox
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.worker.consumer import drain_outbox

if TYPE_CHECKING:
    from backend.services.agents.core.turn_context import TurnContext


def deepagents_available() -> bool:
    try:
        import deepagents  # noqa: F401
        return True
    except ImportError:
        return False


def _model_string(settings) -> str:
    provider = (settings.mindos_default_provider or "google").lower()
    model = settings.mindos_synth_model
    if provider == "google":
        return f"google_genai:{model}"
    if provider == "openai":
        return f"openai:{model}"
    if provider == "anthropic":
        return f"anthropic:{model}"
    return f"google_genai:{model}"


def _guess_tz(message: str) -> str:
    lower = message.lower()
    if "asia/kolkata" in lower or "ist" in lower or "india" in lower:
        return "Asia/Kolkata"
    return "UTC"


def _clock_block(message: str) -> str:
    tz = _guess_tz(message)
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now().astimezone()
    return (
        f"Clock (use for today/tomorrow): {now:%Y-%m-%d %A %H:%M} ({tz}). "
        f"start_iso must use this calendar date for relative words."
    )


def _fs_root() -> Path:
    home = os.environ.get("MINDOS_HOME", "").strip()
    if home:
        return Path(home).expanduser().resolve()
    return Path.cwd().resolve()


def _checkpointer():
    # shortcut: in-memory when postgres checkpointer missing; upgrade for multi-process agents
    try:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()
    except ImportError:
        return None


async def _chunk_preview(session: AsyncSession, artifact_id: str) -> str:
    row = await session.execute(
        text(
            "SELECT LEFT(text, 420) FROM artifact_chunks "
            "WHERE artifact_id = CAST(:aid AS uuid) ORDER BY ord LIMIT 1"
        ),
        {"aid": artifact_id},
    )
    raw = row.scalar_one_or_none()
    return (raw or "").strip().replace("\n", " ")[:420]


def _build_tools(session: AsyncSession, user, settings):
    async def mindos_search(query: str, source: str = "") -> str:
        """Hybrid FTS + vector search over Gmail and notes. Optional source: gmail or manual."""
        src = source.strip().lower() or None
        if src not in (None, "gmail", "manual"):
            src = None
        hits = await hybrid_search_artifacts(
            session, user_id=user.id, query=query, settings=settings, limit=8, source=src
        )
        if not hits:
            return "No matching artifacts."
        lines: list[str] = []
        for h in hits:
            title = h.get("title") or h["artifact_id"]
            preview = await _chunk_preview(session, h["artifact_id"])
            head = f"- [{h.get('source')}] {title} (score {h.get('rank', 0):.3f})"
            lines.append(f"{head}\n  {preview}" if preview else head)
        return "\n".join(lines)

    async def mindos_gmail_sync() -> str:
        """Pull new Gmail into MindOS. Use when mail search is empty or user wants fresh mail."""
        if not get_config().is_enabled("integrations"):
            return "Gmail sync disabled (integrations off in config.json)."
        try:
            stats = await sync_gmail_inbox(session, user=user, settings=settings)
            await session.commit()
        except ValueError as exc:
            return f"Gmail sync failed: {exc}"
        except Exception as exc:
            return f"Gmail sync error: {exc}"
        ingested = int(stats.get("ingested") or 0)
        tail = (
            " Call mindos_process_outbox then mindos_search again."
            if ingested > 0
            else ""
        )
        return (
            f"Gmail sync done: ingested={ingested}, "
            f"skipped={stats.get('skipped', 0)}, scanned={stats.get('scanned', 0)}.{tail}"
        )

    async def mindos_process_outbox(max_events: int = 80) -> str:
        """Embed new mail and run artifact handlers. Call after sync when ingested > 0."""
        cap = max(1, min(int(max_events), 200))
        try:
            n = await drain_outbox(max_events=cap)
        except Exception as exc:
            return f"Outbox processing failed: {exc}"
        if n == 0:
            return "Outbox empty (nothing to process)."
        return f"Processed {n} outbox event(s). Re-run mindos_search."

    async def mindos_commitments() -> str:
        """List open commitments (who owes you a reply or follow-up)."""
        rows = await session.execute(
            select(Commitment.summary)
            .where(Commitment.user_id == user.id, Commitment.status == "open")
            .order_by(Commitment.created_at.desc())
            .limit(12)
        )
        items = [s for (s,) in rows.all() if s]
        return "\n".join(f"- {s}" for s in items) if items else "No open commitments."

    async def mindos_calendar_create_event(
        title: str,
        start_iso: str,
        end_iso: str = "",
        description: str = "",
        location: str = "",
        time_zone: str = "UTC",
        calendar_id: str = "primary",
    ) -> str:
        """Create a Google Calendar event for the connected user.

        Required: title, start_iso (e.g. 2026-11-20T15:00 or with offset).
        If end_iso is empty, duration defaults to 30 minutes.
        time_zone applies when start_iso has no offset (IANA name, e.g. Asia/Kolkata).
        Call when title and start time are known from the user or recent conversation.
        Use the user's IANA timezone when given. Default duration 30 minutes.
        """
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        try:
            event = await create_calendar_event(
                session,
                user=user,
                settings=settings,
                title=title,
                start_iso=start_iso,
                end_iso=end_iso,
                description=description,
                location=location,
                calendar_id=calendar_id,
                time_zone=time_zone,
            )
            await session.commit()
        except ValueError as exc:
            return f"Calendar create failed: {exc}"
        except Exception as exc:
            return f"Calendar create error: {exc}"
        summary = event.get("summary") or title
        start = (event.get("start") or {}).get("dateTime") or start_iso
        link = event.get("htmlLink") or ""
        base = f"Created calendar event: {summary} at {start}."
        if not link:
            return base
        short = await shorten_url(link)
        return f"{base} Link: {short}"

    return [
        trace_tool(mindos_search),
        trace_tool(mindos_gmail_sync),
        trace_tool(mindos_process_outbox),
        trace_tool(mindos_commitments),
        trace_tool(mindos_calendar_create_event),
    ]


async def run_deep_agent(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    intent: str,
    thread_id: uuid.UUID,
    turn_context: TurnContext | None = None,
    history: list[dict] | None = None,
    on_partial: Callable[..., None] | None = None,
) -> dict:
    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return {
            "answer": (
                "Deep agent needs a live LLM. Set MINDOS_LLM_MODE=live (or mock) "
                "and install: uv sync --extra agents"
            ),
            "mode": "deep_unavailable",
            "intent": intent,
        }
    if not deepagents_available():
        return {
            "answer": (
                f"deepagents not installed for {sys.executable} — "
                "run: make sync-agents  then: .venv/bin/mindos chat"
            ),
            "mode": "deep_unavailable",
            "intent": intent,
        }

    from deepagents import create_deep_agent
    from langchain.agents.middleware import TodoListMiddleware

    tools = _build_tools(session, user, settings)
    middleware = [TodoListMiddleware()]
    kwargs: dict = {
        "model": _model_string(settings),
        "tools": tools,
        "middleware": middleware,
        "system_prompt": (
            "MindOS deep agent: answer from Preloaded context and tool results only; never invent mail. "
            "Before calling mindos_gmail_sync or mindos_process_outbox, send the user one short sentence "
            "about what you are about to do; then call the tool. "
            "Use mindos_search for gaps. For mail tasks with no hits: mindos_gmail_sync → "
            "mindos_process_outbox if ingested > 0 → mindos_search (at most one extra sync cycle). "
            "If Steps already taken were completed before you started, summarize them in your opening. "
            "Tables/lists only from retrieved data. Be concise; finish every sentence. "
            "Use write_todos for multi-step work. Stay in workspace root. "
            "Calendar: once title and start time are known (including from the thread), "
            "call mindos_calendar_create_event—do not pretend it was created. "
            "Two or more events in one request: write_todos listing each, then one "
            "mindos_calendar_create_event per todo; list every link in the reply. "
            "Ask at most one missing detail; then call the tool. "
            "If Google is not connected, point to /v1/integrations/google/start. "
            "Always repeat each tool Link once (plain text, no markdown link syntax)."
        ),
    }
    if intent == "execute":
        from deepagents.backends import FilesystemBackend

        kwargs["backend"] = FilesystemBackend(root_dir=str(_fs_root()), virtual_mode=True)

    checkpointer = _checkpointer()
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer

    agent = create_deep_agent(**kwargs)
    config = langsmith_run_config(
        thread_id=str(thread_id),
        run_name=f"mindos_deep_{intent}",
        metadata={"intent": intent, "user_id": str(user.id)},
        tags=["deep_agent", intent],
    )
    from backend.services.agents.core.router_llm import _format_history

    hist_block = _format_history(history)
    user_content = message
    if hist_block:
        user_content = f"Recent conversation:\n{hist_block}\n\nUser task:\n{message}"
    if turn_context is not None and turn_context.has_context:
        ctx_block = clip_text(turn_context.context_text, DEEP_USER_CONTEXT_MAX)
        pre = preflight_prompt_block(turn_context.preflight_actions)
        user_content = f"Preloaded context:\n{ctx_block}"
        if pre:
            user_content += f"\n\n{pre}"
        if hist_block:
            user_content += f"\n\nRecent conversation:\n{hist_block}"
        user_content += f"\n\nUser task:\n{message}"
    elif turn_context is not None and turn_context.preflight_actions:
        pre = preflight_prompt_block(turn_context.preflight_actions)
        user_content = pre
        if hist_block:
            user_content += f"\n\nRecent conversation:\n{hist_block}"
        user_content += f"\n\nUser task:\n{message}"
    user_content = f"{_clock_block(message)}\n\n{user_content}"
    invoke = {"messages": [{"role": "user", "content": user_content}]}
    try:
        if tracing_active():
            from langsmith.run_helpers import get_current_run_tree, trace, tracing_context

            parent = get_current_run_tree()
            trace_ctx = trace(
                name=unique_run_name(f"mindos_deep_{intent}"),
                run_type="chain",
                inputs={
                    "message": message[:4000],
                    "intent": intent,
                    "thread_id": str(thread_id),
                    "turn_trace_id": turn_trace_id(),
                },
                metadata={
                    "thread_id": str(thread_id),
                    "intent": intent,
                    "mindos_turn_trace_id": turn_trace_id(),
                },
            )
            deep_run = trace_ctx.__enter__()
            nest = deep_run if deep_run is not None else parent
            child_ctx = tracing_context(parent=nest) if nest is not None else nullcontext()
            try:
                with child_ctx:
                    result = await _ainvoke_streaming(
                        agent, invoke, config, on_partial=on_partial
                    )
            except Exception:
                if deep_run is not None:
                    trace_ctx.__exit__(*sys.exc_info())
                raise
            answer = normalize_markdown_tables(
                _surface_tool_outputs(_extract_answer(result) or "", result)
            )
            if deep_run is not None:
                deep_run.end(
                    outputs={
                        "answer": (answer or "")[:8000],
                        "mode": "deep_agent",
                        "intent": intent,
                    }
                )
                trace_ctx.__exit__(None, None, None)
        else:
            result = await _ainvoke_streaming(agent, invoke, config, on_partial=on_partial)
            answer = normalize_markdown_tables(
                _surface_tool_outputs(_extract_answer(result) or "", result)
            )
    except Exception as exc:
        flush_traces()
        return {
            "answer": f"Deep agent failed: {exc}",
            "mode": "deep_error",
            "intent": intent,
        }

    flush_traces()
    sources = turn_context.sources if turn_context else []
    return {
        "answer": answer or "(empty agent reply)",
        "mode": "deep_agent",
        "intent": intent,
        "sources": sources,
    }


_TODO_ICON = {
    "in_progress": "→",
    "pending": "○",
    "completed": "✓",
    "done": "✓",
}


def _tool_call_name(tc) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name") or "")
    return str(getattr(tc, "name", "") or "")


def _tool_call_args(tc) -> dict:
    if isinstance(tc, dict):
        raw = tc.get("args") or {}
    else:
        raw = getattr(tc, "args", None) or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _latest_todos(msgs) -> list[dict] | None:
    latest: list[dict] | None = None
    for m in msgs or []:
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls is None and isinstance(m, dict):
            tool_calls = m.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                if _tool_call_name(tc) != "write_todos":
                    continue
                todos = _tool_call_args(tc).get("todos")
                if isinstance(todos, list) and todos:
                    latest = todos
        name = getattr(m, "name", None)
        if name is None and isinstance(m, dict):
            name = m.get("name")
        if name != "write_todos":
            continue
        try:
            data = json.loads(_message_text(m))
        except json.JSONDecodeError:
            continue
        if isinstance(data.get("todos"), list):
            latest = data["todos"]
    return latest


def _format_todos_think(todos: list[dict]) -> str:
    lines = ["Plan:"]
    for item in todos:
        status = str(item.get("status") or "pending").lower()
        icon = _TODO_ICON.get(status, "○")
        label = status.replace("_", " ")
        content = (item.get("content") or "").strip()
        lines.append(f"{icon} {content} ({label})")
    return "\n".join(lines)


def _emit_todo_think(msgs, on_partial, *, last_sig: list[str]) -> None:
    todos = _latest_todos(msgs)
    if not todos:
        return
    text = _format_todos_think(todos)
    if text == last_sig[0]:
        return
    last_sig[0] = text
    emit_partial(on_partial, text, phase="think")


async def _ainvoke_streaming(agent, invoke, config, *, on_partial) -> dict:
    last_sig: list[str] = [""]
    result: dict | None = None
    async for state in agent.astream(invoke, config=config, stream_mode="values"):
        if isinstance(state, dict):
            result = state
            _emit_todo_think(state.get("messages"), on_partial, last_sig=last_sig)
    if result is not None:
        return result
    return await agent.ainvoke(invoke, config=config)


_TOOL_URL = re.compile(r"https?://[^\s\]\)]+")
_EID_IN_URL = re.compile(r"eid=([^&\s\]\)]+)")


def _url_already_in_answer(url: str, answer: str) -> bool:
    if url in answer:
        return True
    m = _EID_IN_URL.search(url)
    return bool(m and m.group(1) in answer)


def _message_text(msg) -> str:
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    return ""


def _is_tool_message(msg) -> bool:
    kind = getattr(msg, "type", None)
    if kind == "tool":
        return True
    if isinstance(msg, dict) and msg.get("type") == "tool":
        return True
    return type(msg).__name__ == "ToolMessage"


def _tool_texts(result) -> list[str]:
    if not isinstance(result, dict):
        return []
    out: list[str] = []
    for msg in result.get("messages") or []:
        if not _is_tool_message(msg):
            continue
        text = _message_text(msg).strip()
        if text:
            out.append(text)
    return out


def _surface_tool_outputs(answer: str, result) -> str:
    """Append tool URLs only when the model reply omitted them (matches by eid too)."""
    tool_texts = _tool_texts(result)
    if not tool_texts:
        return answer
    extras: list[str] = []
    for text in tool_texts:
        for url in _TOOL_URL.findall(text):
            if _url_already_in_answer(url, answer) or any(
                _url_already_in_answer(url, u) for u in extras
            ):
                continue
            extras.append(url)
    if not extras:
        return answer
    base = answer.rstrip()
    return f"{base}\n\n{extras[0]}" if len(extras) == 1 else f"{base}\n\n" + "\n".join(extras)


def _extract_answer(result) -> str:
    if isinstance(result, dict):
        msgs = result.get("messages") or []
        for m in reversed(msgs):
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            if isinstance(content, list):
                parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                ]
                joined = "".join(parts).strip()
                if joined:
                    return joined
    return clip_text(str(result), 12_000, min_keep=4000)