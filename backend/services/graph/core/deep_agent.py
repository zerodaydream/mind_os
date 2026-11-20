"""Deep Agents (LangGraph) for plan + execute intents."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from contextlib import nullcontext

from backend.common.langsmith_setup import (
    flush_traces,
    langsmith_run_config,
    tracing_active,
    turn_trace_id,
    unique_run_name,
)
from backend.common.utils.enums import LlmMode
from backend.services.agents.core.render.chat_stream import (
    THINK_PLAN_HEADER,
    emit_chat_stream,
    merge_chat_stream_text,
)
from backend.services.agents.core.render.prompts import ANSWER_LAYOUT_CORE
from backend.services.agents.core.render.markdown_tables import (
    normalize_markdown_tables,
    polish_agent_answer,
)
from backend.services.agents.core.render.tool_status import tool_status_line
from backend.services.agents.core.turn.turn_retrieval import preflight_prompt_block
from backend.services.integrations.core.calendar.scheduling import (
    collect_calendar_thread,
    mentions_tomorrow,
)
from backend.services.integrations.core.calendar.timezone import resolve_user_timezone
from backend.services.integrations.core.calendar.events import (
    google_calendar_auth_status,
    tz_display_abbrev,
)

if TYPE_CHECKING:
    from backend.services.agents.core.turn.turn_retrieval import TurnRetrieval


def deepagents_available() -> bool:
    """Return True when the deepagents package is importable."""
    try:
        import deepagents  # noqa: F401
        return True
    except ImportError:
        return False


def _model_string(settings) -> str:
    """Build the LangChain model id string from settings."""
    provider = (settings.mindos_default_provider or "google").lower()
    model = settings.mindos_synth_model
    if provider == "google":
        return f"google_genai:{model}"
    if provider == "openai":
        return f"openai:{model}"
    if provider == "anthropic":
        return f"anthropic:{model}"
    return f"google_genai:{model}"


def _chat_model(settings):
    """LangChain chat model; Google uses streaming thoughts when configured."""
    provider = (settings.mindos_default_provider or "google").lower()
    model = settings.mindos_synth_model
    if provider != "google":
        return _model_string(settings)
    from langchain_google_genai import ChatGoogleGenerativeAI
    from backend.services.llm.providers.google import build_gemini_thinking_config

    thinking = build_gemini_thinking_config(
        model,
        settings.mindos_thinking_level,
        budget=settings.mindos_thinking_budget,
    )
    kwargs: dict = {"model": model, "google_api_key": settings.google_api_key}
    if thinking:
        kwargs["thinking_config"] = thinking
        kwargs["include_thoughts"] = True
    return ChatGoogleGenerativeAI(**kwargs)


def _clock_block(task: str, tz: str) -> str:
    """Prompt block with now/tomorrow in the user timezone for calendar tasks."""
    try:
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now().astimezone()
    tomorrow = now + timedelta(days=1)
    pending = ""
    if mentions_tomorrow(task):
        pending = " User intent includes tomorrow — use Tomorrow= date below. "
    tz_label = tz_display_abbrev(tz) or tz
    return (
        f"Clock now={now:%Y-%m-%d %A %H:%M} {tz_label} ({tz}). "
        f"Tomorrow={tomorrow:%Y-%m-%d %A}.{pending} "
        f"time_zone={tz}. User-facing times must include the zone label "
        f"(e.g. 4 PM {tz_label}). Prefer mindos_calendar_create_event with title/time; "
        "server resolves start from user thread when possible."
    )


def _oauth_start_url(settings) -> str:
    """Absolute or relative Google OAuth start URL from redirect settings."""
    base = (settings.google_oauth_redirect_uri or "").rsplit("/callback", 1)[0]
    return f"{base}/start" if base else "/v1/integrations/google/start"


_OAUTH_ACK = frozenset({"yes", "y", "ok", "okay", "sure", "done", "connected", "ready"})


def _user_oauth_signal(message: str) -> str | None:
    """Detect short user replies that continue an OAuth flow."""
    lower = message.strip().lower()
    if lower in _OAUTH_ACK:
        return lower
    if any(p in lower for p in ("signed in", "i connected", "oauth done", "finished sign")):
        return "done"
    if lower.startswith("yes"):
        return "yes"
    return None


def _assistant_sent_oauth_link(history: list[dict] | None) -> bool:
    """True if a recent assistant message included the OAuth start link."""
    if not history:
        return False
    for m in reversed(history[-8:]):
        if m.get("role") != "assistant":
            continue
        if "/integrations/google/start" in (m.get("content") or ""):
            return True
    return False


def _oauth_continuation_turn(message: str, history: list[dict] | None) -> bool:
    """True when the user is acknowledging or continuing Google sign-in."""
    sig = _user_oauth_signal(message)
    if not sig:
        return False
    if sig in {"done", "connected", "ready"}:
        return _assistant_sent_oauth_link(history)
    return sig == "yes"


def should_route_deep_for_oauth(message: str, history: list[dict] | None) -> bool:
    """Route plan/execute to deep agent after OAuth nudges."""
    return _oauth_continuation_turn(message, history)


def _message_mentions_calendar(message: str) -> bool:
    """True if the user message looks calendar-related."""
    from backend.services.integrations.core.calendar.scheduling import line_looks_calendar_related

    return line_looks_calendar_related(message)


def _history_mentions_calendar(history: list[dict] | None) -> bool:
    """True if recent user turns mention scheduling or calendar."""
    if not history:
        return False
    for m in reversed(history[-12:]):
        if m.get("role") != "user":
            continue
        if _message_mentions_calendar(m.get("content") or ""):
            return True
    return False


async def run_deep_agent(
    session: AsyncSession,
    user,
    settings,
    gateway,
    message: str,
    *,
    intent: str,
    thread_id: uuid.UUID,
    turn_retrieval: TurnRetrieval | None = None,
    history: list[dict] | None = None,
    on_partial: Callable[..., None] | None = None,
    client_tz: str = "",
) -> dict:
    """Run Deep Agents for plan/execute; stream partials and return answer metadata."""
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

    calendar_task = collect_calendar_thread(history, message)
    default_tz = await resolve_user_timezone(
        session,
        user=user,
        settings=settings,
        message=calendar_task,
        client_tz=client_tz,
    )
    from backend.services.integrations.mcp.registry import load_mcp_for_agent

    tools, mcp_guidance = await load_mcp_for_agent(
        session,
        user,
        settings,
        default_tz=default_tz,
        user_task=(message or "").strip() or calendar_task,
        gateway=gateway,
        on_partial=on_partial,
    )
    middleware = [TodoListMiddleware()]
    from backend.services.context.prompts import load_prompt_module

    system_prompt = load_prompt_module("core_system")
    system_prompt += (
        "\nYou have tools for search, calendar_query, calendar_change, commitments, and read_url. "
        "Use search before answering mail questions. Prefer Evidence [E#] citations. "
        "Sync and outbox are scheduled infrastructure — never call them. "
        "Delegate via task(): researcher (multi-hop evidence), calendar-operator (schedule writes), "
        "file-scout (local disk when index misses). Subagents return short findings only. "
        "Session workspace: /NOTES.md and /SESSION.md; project tree at /project/; durable scratch at /store/. "
        "Use write_todos for multi-step work. "
        f"{ANSWER_LAYOUT_CORE}"
    )
    if mcp_guidance:
        system_prompt = f"{system_prompt}\n{mcp_guidance}"
    kwargs: dict = {
        "model": _chat_model(settings),
        "tools": tools,
        "middleware": middleware,
        "system_prompt": system_prompt,
    }
    from backend.services.graph.core.deep_session import (
        build_deep_backend,
        deep_checkpointer,
        deep_store,
        memory_sources_for_thread,
    )

    kwargs["backend"] = build_deep_backend(thread_id)
    kwargs["memory"] = memory_sources_for_thread(thread_id)
    checkpointer = deep_checkpointer()
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    store = deep_store()
    if store is not None:
        kwargs["store"] = store

    from backend.services.graph.core.subagents import build_subagents

    kwargs["subagents"] = build_subagents(
        personal_tools=tools,
        on_partial=on_partial,
        user_task=(message or "").strip() or calendar_task,
    )
    kwargs["interrupt_on"] = {
        "calendar_change": True,
        "mindos_calendar_delete_event": True,
        "mindos_fs_delete": True,
        "mindos_fs_write": True,
        "mindos_fs_rename": True,
    }

    agent = create_deep_agent(**kwargs)
    tags = ["deep_agent", intent]
    if any("_fetch_" in (getattr(t, "name", "") or "") or (getattr(t, "name", "") or "").startswith("fetch_") for t in tools):
        tags.append("mcp")
    config = langsmith_run_config(
        thread_id=str(thread_id),
        run_name=f"mindos_deep_{intent}",
        metadata={"intent": intent, "user_id": str(user.id)},
        tags=tags,
    )
    from backend.services.agents.core.contracts.from_turn_retrieval import (
        evidence_pack_from_turn_retrieval,
    )
    from backend.services.context.compiler import format_evidence_block

    pack = None
    if turn_retrieval is not None:
        pack = getattr(turn_retrieval, "evidence_pack", None)
        if pack is None:
            pack = evidence_pack_from_turn_retrieval(turn_retrieval)
    turn_system = system_prompt
    if pack and pack.items:
        turn_system += "\n\n" + format_evidence_block(pack)
    if turn_retrieval is not None and turn_retrieval.preflight_actions:
        pre = preflight_prompt_block(turn_retrieval.preflight_actions)
        if pre:
            turn_system += f"\n\n{pre}"
    kwargs["system_prompt"] = turn_system
    agent = create_deep_agent(**kwargs)
    user_content = f"{_clock_block(calendar_task, default_tz)}\n\n{message}"
    oauth_url = _oauth_start_url(settings)
    calendar_thread = (
        _message_mentions_calendar(message)
        or _history_mentions_calendar(history)
        or _oauth_continuation_turn(message, history)
    )
    calendar_finalize = _message_mentions_calendar(message) or _oauth_continuation_turn(
        message, history
    )
    auth_status = await google_calendar_auth_status(session, user)
    if calendar_thread and auth_status != "ok":
        return {
            "answer": (
                "Google Calendar needs a quick browser sign-in.\n\n"
                f"Open:\n{oauth_url}\n\nReply **done** when finished."
            ),
            "mode": "deep_agent",
            "intent": intent,
            "sources": [],
        }

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
                        agent,
                        invoke,
                        config,
                        on_partial=on_partial,
                    )
            except BaseException:
                if deep_run is not None:
                    trace_ctx.__exit__(*sys.exc_info())
                raise
            answer = normalize_markdown_tables(
                polish_agent_answer(
                    _finalize_calendar_answer(
                        _surface_tool_outputs(_extract_answer(result) or "", result),
                        result,
                        oauth_url=oauth_url,
                        calendar_turn=calendar_finalize,
                    )
                )
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
            result = await _ainvoke_streaming(
                agent,
                invoke,
                config,
                on_partial=on_partial,
            )
            answer = normalize_markdown_tables(
                polish_agent_answer(
                    _finalize_calendar_answer(
                        _surface_tool_outputs(_extract_answer(result) or "", result),
                        result,
                        oauth_url=oauth_url,
                        calendar_turn=calendar_finalize,
                    )
                )
            )
    except Exception as exc:
        flush_traces()
        return {
            "answer": f"Deep agent failed: {exc}",
            "mode": "deep_error",
            "intent": intent,
        }

    flush_traces()
    from backend.services.graph.core.deep_session import append_session_turn

    todos = _latest_todos(result.get("messages") if isinstance(result, dict) else None)
    try:
        append_session_turn(
            thread_id,
            user_message=message,
            answer=answer or "",
            todos=todos,
        )
    except OSError:
        pass
    return {
        "answer": answer or "(empty agent reply)",
        "mode": "deep_agent",
        "intent": intent,
        "sources": [],
    }


def _answer_leaks_system_prompt(answer: str) -> bool:
    """Heuristic: assistant echoed internal instructions."""
    low = (answer or "").lower()
    return any(
        p in low
        for p in (
            "per instructions",
            "as per the instructions",
            "the user request states",
            "the user requested",
            "if calendar tools report",
            "never quote",
            "no create without",
        )
    )


def _replace_relative_oauth_paths(answer: str, oauth_url: str) -> str:
    """Rewrite relative OAuth paths in answers to absolute URLs."""
    return (answer or "").replace("/v1/integrations/google/start", oauth_url)


def _finalize_calendar_answer(
    answer: str,
    result,
    *,
    oauth_url: str,
    calendar_turn: bool,
    settings=None,
    auth_status: str = "ok",
) -> str:
    """Inject OAuth link, calendar auth hints, and strip unsafe output."""
    del settings, auth_status
    if not calendar_turn:
        return answer
    for text in _current_turn_tool_texts(result):
        head = text.lstrip()
        if head.startswith("Created calendar event") or head.startswith("Updated calendar event"):
            return text
        if "permission missing" in text.lower() or "not connected" in text.lower():
            return f"{text}\n\nOpen:\n{oauth_url}\n\nReply **done** when finished."
    if _answer_leaks_system_prompt(answer):
        for text in _current_turn_tool_texts(result):
            if text.lstrip().startswith("Created calendar event"):
                return text
        return "What time should I use for the calendar event?"
    return _replace_relative_oauth_paths(answer, oauth_url)


_TODO_ICON = {
    "in_progress": "●",
    "pending": "○",
    "completed": "✓",
    "done": "✓",
}


_TURN_TRACE_SUFFIX = re.compile(r"^(.+)_[a-f0-9]{10}$")


def _canonical_tool_name(name: str) -> str:
    """Normalize MCP/tool names for display and dedupe."""
    n = (name or "").strip()
    m = _TURN_TRACE_SUFFIX.match(n)
    return m.group(1) if m else n


def _tool_call_name(tc) -> str:
    """Extract tool name from a LangChain tool call object."""
    if isinstance(tc, dict):
        raw = str(tc.get("name") or "")
    else:
        raw = str(getattr(tc, "name", "") or "")
    return _canonical_tool_name(raw)


def _tool_call_args(tc) -> dict:
    """Extract args dict from a LangChain tool call object."""
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


def _is_human_message(msg) -> bool:
    """True when the message is from the user/human role."""
    kind = getattr(msg, "type", None)
    if kind in ("human", "user"):
        return True
    if isinstance(msg, dict) and msg.get("type") in ("human", "user"):
        return True
    role = getattr(msg, "role", None)
    if role in ("human", "user"):
        return True
    if isinstance(msg, dict) and msg.get("role") in ("human", "user"):
        return True
    return type(msg).__name__ == "HumanMessage"


def _messages_after_last_human(msgs) -> list:
    """LangGraph messages after the latest human input (this turn only)."""
    if not msgs:
        return []
    start = 0
    for i in range(len(msgs) - 1, -1, -1):
        if _is_human_message(msgs[i]):
            start = i + 1
            break
    return list(msgs[start:])


def _latest_todos(msgs) -> list[dict] | None:
    """Read the newest todo list from the current turn's agent state."""
    latest: list[dict] | None = None
    for m in _messages_after_last_human(msgs):
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
    """Format todos for the thinking/status partial stream."""
    lines = [THINK_PLAN_HEADER]
    for item in todos:
        if isinstance(item, str):
            content, status = item.strip(), "pending"
        else:
            content = (item.get("content") or "").strip()
            status = str(item.get("status") or "pending").lower()
        icon = _TODO_ICON.get(status, "○")
        label = status.replace("_", " ")
        lines.append(f"{icon} {content} ({label})")
    return "\n".join(lines)


def _emit_todo_think(msgs, on_partial, *, last_sig: list[str]) -> None:
    """Push todo updates to on_partial when the list changes."""
    todos = _latest_todos(msgs)
    if not todos:
        return
    text = _format_todos_think(todos)
    if text == last_sig[0]:
        return
    last_sig[0] = text
    emit_chat_stream(on_partial, text, phase="think")


def _thinking_text_from_message(msg) -> str:
    """Gemini/LC thinking blocks on AIMessage (often before tool_calls, not token-streamed)."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if not isinstance(content, list):
        return ""
    bits: list[str] = []
    for p in content:
        if not isinstance(p, dict):
            continue
        if p.get("type") not in ("thinking", "reasoning") and not p.get("thought"):
            continue
        text = (p.get("thinking") or p.get("text") or "").strip()
        if text:
            bits.append(text)
    return "\n\n".join(bits)


def _latest_ai_message(msgs) -> object | None:
    if not msgs:
        return None
    for m in reversed(msgs):
        if _is_tool_message(m):
            continue
        if "Human" in type(m).__name__:
            continue
        if "AI" in type(m).__name__:
            return m
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if role in ("ai", "assistant"):
            return m
    return None


def _emit_model_step_thinking(
    msgs,
    on_partial,
    *,
    think_acc: list[str],
    think_step_sig: list[str],
    think_from_stream: list[bool] | None = None,
) -> None:
    """Surface provider thinking between agent steps (per LangGraph/Gemini AIMessage blocks)."""
    if on_partial is None:
        return
    m = _latest_ai_message(msgs)
    if m is None:
        return
    tool_calls = getattr(m, "tool_calls", None)
    if tool_calls is None and isinstance(m, dict):
        tool_calls = m.get("tool_calls")
    if think_from_stream and think_from_stream[0] and not tool_calls:
        return
    text = _thinking_text_from_message(m)
    if not text or text == think_step_sig[0]:
        return
    if think_acc[0] and (text in think_acc[0] or think_acc[0] in text):
        think_step_sig[0] = text
        return
    think_step_sig[0] = text
    if think_acc[0]:
        think_acc[0] = f"{think_acc[0].rstrip()}\n\n{text}"
    else:
        think_acc[0] = text
    emit_chat_stream(on_partial, think_acc[0], phase="think")


def _emit_tool_think(msgs, on_partial, *, seen: set[str]) -> None:
    """Push new tool calls to on_partial once per call id."""
    if not msgs or on_partial is None:
        return
    for m in reversed(msgs):
        tool_calls = getattr(m, "tool_calls", None)
        if tool_calls is None and isinstance(m, dict):
            tool_calls = m.get("tool_calls")
        if not tool_calls:
            kind = getattr(m, "type", None) or (m.get("type") if isinstance(m, dict) else None)
            if kind != "ai" and "AI" not in type(m).__name__:
                continue
            break
        for tc in tool_calls:
            tid = _tool_call_name(tc)
            if isinstance(tc, dict):
                key = str(tc.get("id") or f"{tid}:{_tool_call_args(tc)}")
            else:
                key = str(getattr(tc, "id", None) or f"{tid}:{_tool_call_args(tc)}")
            if key in seen:
                continue
            seen.add(key)
            name = _canonical_tool_name(tid)
            emit_chat_stream(
                on_partial,
                f"[tool] {tool_status_line(name)}…",
                phase="think",
            )
        break


def _is_ai_stream_chunk(msg) -> bool:
    """True for assistant content chunks during astream."""
    if _is_tool_message(msg):
        return False
    if getattr(msg, "tool_calls", None) or getattr(msg, "tool_call_chunks", None):
        return False
    name = type(msg).__name__
    if "Human" in name:
        return False
    return "AI" in name


def _maybe_reset_answer_stream(msgs, buf: list[str]) -> None:
    """Clear streamed answer buffer when a new AI message starts."""
    if not msgs:
        return
    last = msgs[-1]
    if _is_tool_message(last) or getattr(last, "tool_calls", None):
        buf.clear()


async def _ainvoke_values_only(agent, invoke, config, *, on_partial) -> dict:
    """Invoke agent without token streaming; still emit tool/todo think."""
    last_sig: list[str] = [""]
    think_acc = [""]
    think_step_sig = [""]
    tool_seen: set[str] = set()
    result: dict | None = None
    async for state in agent.astream(invoke, config=config, stream_mode="values"):
        if isinstance(state, dict):
            result = state
            msgs = state.get("messages")
            _emit_model_step_thinking(
                msgs, on_partial, think_acc=think_acc, think_step_sig=think_step_sig
            )
            _emit_todo_think(msgs, on_partial, last_sig=last_sig)
            _emit_tool_think(msgs, on_partial, seen=tool_seen)
    if result is not None:
        return result
    return await agent.ainvoke(invoke, config=config)


async def _ainvoke_streaming(agent, invoke, config, *, on_partial) -> dict:
    """Invoke agent with astream; stream answer tokens to on_partial."""
    last_sig: list[str] = [""]
    tool_seen: set[str] = set()
    answer_buf: list[str] = []
    think_acc = [""]
    think_step_sig = [""]
    think_from_stream = [False]
    result: dict | None = None
    try:
        async for part in agent.astream(
            invoke,
            config=config,
            stream_mode=["messages", "values"],
            version="v2",
        ):
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "values":
                data = part.get("data")
                if isinstance(data, dict):
                    result = data
                    msgs = data.get("messages")
                    _maybe_reset_answer_stream(msgs, answer_buf)
                    _emit_model_step_thinking(
                        msgs,
                        on_partial,
                        think_acc=think_acc,
                        think_step_sig=think_step_sig,
                        think_from_stream=think_from_stream,
                    )
                    _emit_todo_think(msgs, on_partial, last_sig=last_sig)
                    _emit_tool_think(msgs, on_partial, seen=tool_seen)
                continue
            if kind != "messages":
                continue
            payload = part.get("data")
            if not payload:
                continue
            msg = payload[0] if isinstance(payload, (list, tuple)) else payload
            if not _is_ai_stream_chunk(msg):
                continue
            streamed = _message_stream_piece(msg)
            if not streamed:
                continue
            kind, piece = streamed
            if kind == "think":
                think_from_stream[0] = True
                think_acc[0] = merge_chat_stream_text(think_acc[0], piece)
                if think_acc[0]:
                    emit_chat_stream(on_partial, think_acc[0], phase="think")
            else:
                answer_buf.append(piece)
                text = "".join(answer_buf).strip()
                if text:
                    emit_chat_stream(on_partial, text, phase="answer")
    except asyncio.CancelledError:
        raise
    except Exception:
        return await _ainvoke_values_only(agent, invoke, config, on_partial=on_partial)
    if result is not None:
        return result
    return await agent.ainvoke(invoke, config=config)


_TOOL_URL = re.compile(r"https?://[^\s\]\)]+")
_EID_IN_URL = re.compile(r"eid=([^&\s\]\)]+)")
_CALENDAR_EVENT_URL = re.compile(
    r"https?://(?:www\.)?google\.com/calendar/event\b",
    re.IGNORECASE,
)


def _url_already_in_answer(url: str, answer: str) -> bool:
    """True if a URL already appears in the final answer text."""
    if url in answer:
        return True
    m = _EID_IN_URL.search(url)
    return bool(m and m.group(1) in answer)


def _message_text(msg) -> str:
    """Extract plain answer text from a LangGraph message object."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(str(p))
                continue
            if p.get("type") in ("thinking", "reasoning") or p.get("thought"):
                continue
            parts.append(str(p.get("text") or ""))
        return "".join(parts)
    return ""


def _message_stream_piece(msg) -> tuple[str, str] | None:
    """One streamed token: (think|answer, text)."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, list):
        for p in content:
            if not isinstance(p, dict):
                continue
            text = (p.get("thinking") or p.get("text") or "").strip()
            if not text:
                continue
            if p.get("type") in ("thinking", "reasoning") or p.get("thought"):
                return "think", text
            return "answer", text
    text = _message_text(msg).strip()
    return ("answer", text) if text else None


def _is_tool_message(msg) -> bool:
    """True when the message is a tool result."""
    kind = getattr(msg, "type", None)
    if kind == "tool":
        return True
    if isinstance(msg, dict) and msg.get("type") == "tool":
        return True
    return type(msg).__name__ == "ToolMessage"


def _tool_texts(result) -> list[str]:
    """Collect string payloads from all tool messages in state."""
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


def _current_turn_tool_texts(result) -> list[str]:
    """Tool outputs since the last human message this turn."""
    if not isinstance(result, dict):
        return []
    chunk: list[str] = []
    for msg in reversed(result.get("messages") or []):
        if _is_human_message(msg):
            break
        if not _is_tool_message(msg):
            continue
        text = _message_text(msg).strip()
        if text:
            chunk.append(text)
    chunk.reverse()
    return chunk


def _surfaceable_urls(text: str) -> list[str]:
    """Calendar create/update links only—not list dumps or install/docs URLs."""
    urls = _TOOL_URL.findall(text)
    if not urls:
        return []
    cal = [u for u in urls if _CALENDAR_EVENT_URL.search(u)]
    if not cal:
        return []
    head = text.lstrip()
    if head.startswith("Created calendar event") or head.startswith(
        "Updated calendar event"
    ):
        return [cal[0]]
    if len(cal) == 1:
        return cal
    return []


def _surface_tool_outputs(answer: str, result) -> str:
    """Append tool URLs only when the model reply omitted them (matches by eid too)."""
    tool_texts = _current_turn_tool_texts(result)
    if not tool_texts:
        return answer
    extras: list[str] = []
    for text in tool_texts:
        for url in _surfaceable_urls(text):
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
    """Pick the final assistant string from graph result messages."""
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