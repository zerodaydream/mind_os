"""LangSmith env + small helpers (LangChain reads os.environ)."""

from __future__ import annotations

import contextvars
import os
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from backend.common.config_loader.settings import Settings

_configured = False
_turn_trace_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mindos_turn_trace_id", default=None
)

F = TypeVar("F", bound=Callable[..., Any])


def tracing_active() -> bool:
    return os.environ.get("LANGCHAIN_TRACING_V2", "").lower() in ("true", "1", "yes")


def begin_turn_trace() -> str:
    """Per chat turn — stored in LangSmith metadata (mindos_turn_trace_id), not run names."""
    trace_id = uuid.uuid4().hex[:10]
    _turn_trace_token.set(trace_id)
    return trace_id


def turn_trace_id() -> str | None:
    return _turn_trace_token.get()


def unique_run_name(base: str) -> str:
    """Stable run/tool labels in LangSmith and the chat UI; disambiguate turns via metadata."""
    return base


def flush_traces() -> None:
    if not tracing_active():
        return
    try:
        from langsmith import Client

        Client().flush(timeout=15.0)
    except Exception:
        pass


def reset_langsmith_config() -> None:
    global _configured
    _configured = False


def _use_agent_addressing(settings: Settings) -> bool:
    agent_id = (settings.langsmith_agent_id or os.environ.get("LANGSMITH_AGENT_ID", "")).strip()
    agent_env = (
        settings.langsmith_agent_environment or os.environ.get("LANGSMITH_AGENT_ENVIRONMENT", "")
    ).strip()
    return bool(agent_id and agent_env)


def configure_langsmith(settings: Settings) -> bool:
    global _configured
    if _configured:
        return tracing_active()
    _configured = True
    if not settings.langsmith_tracing:
        return False
    key = (settings.langsmith_api_key or os.environ.get("LANGSMITH_API_KEY", "")).strip()
    if not key:
        return False
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ["LANGSMITH_API_KEY"] = key
    endpoint = (settings.langsmith_endpoint or "").strip()
    if endpoint:
        os.environ.setdefault("LANGSMITH_ENDPOINT", endpoint)

    if _use_agent_addressing(settings):
        agent_id = (
            settings.langsmith_agent_id or os.environ.get("LANGSMITH_AGENT_ID", "")
        ).strip()
        agent_env = (
            settings.langsmith_agent_environment or os.environ.get("LANGSMITH_AGENT_ENVIRONMENT", "")
        ).strip().lower()
        for legacy in (
            "LANGSMITH_PROJECT",
            "LANGCHAIN_PROJECT",
            "LANGCHAIN_SESSION",
        ):
            os.environ.pop(legacy, None)
        os.environ["LANGSMITH_AGENT_ID"] = agent_id
        os.environ["LANGSMITH_AGENT_ENVIRONMENT"] = agent_env
    else:
        os.environ.pop("LANGSMITH_AGENT_ID", None)
        os.environ.pop("LANGSMITH_AGENT_ENVIRONMENT", None)
        project = (settings.langsmith_project or "mind-os").strip()
        os.environ.setdefault("LANGSMITH_PROJECT", project)
    return True


def langsmith_run_config(
    *,
    thread_id: str,
    run_name: str,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
    if not tracing_active():
        return cfg
    cfg["run_name"] = unique_run_name(run_name)
    meta = dict(metadata or {})
    meta.setdefault("thread_id", thread_id)
    tid = turn_trace_id()
    if tid:
        meta.setdefault("mindos_turn_trace_id", tid)
    cfg["metadata"] = meta
    if tags:
        cfg["tags"] = tags
    return cfg


def trace_tool(fn: F) -> F:
    """Wrap a MindOS tool so LangSmith shows TOOL runs with name + I/O."""
    tool_name = fn.__name__

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        if not tracing_active():
            return await fn(*args, **kwargs)
        try:
            from langsmith.run_helpers import trace
        except ImportError:
            return await fn(*args, **kwargs)
        ls_cm = trace(
            name=unique_run_name(tool_name),
            run_type="tool",
            inputs={
                "kwargs": {
                    k: str(v)[:400] for k, v in list(kwargs.items())[:12]
                }
            },
        )
        ls_run = ls_cm.__enter__()
        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            if ls_run is not None:
                ls_run.end(error=str(exc))
            ls_cm.__exit__(type(exc), exc, exc.__traceback__)
            raise
        if ls_run is not None:
            ls_run.end(outputs={"result": str(result)[:2000]})
        ls_cm.__exit__(None, None, None)
        return result

    return wrapper  # type: ignore[return-value]


class McpLangSmithInterceptor:
    """LangSmith TOOL runs for MCP stdio/http tool calls (langchain-mcp-adapters)."""

    async def __call__(self, request, handler):
        if not tracing_active():
            return await handler(request)
        try:
            from langsmith.run_helpers import trace
        except ImportError:
            return await handler(request)
        run_name = unique_run_name(f"mcp_{request.server_name}_{request.name}")
        ls_cm = trace(
            name=run_name,
            run_type="tool",
            inputs={
                "mcp_server": request.server_name,
                "mcp_tool": request.name,
                "args": {
                    k: str(v)[:400] for k, v in list((request.args or {}).items())[:12]
                },
            },
        )
        ls_run = ls_cm.__enter__()
        try:
            result = await handler(request)
        except Exception as exc:
            if ls_run is not None:
                ls_run.end(error=str(exc))
            ls_cm.__exit__(type(exc), exc, exc.__traceback__)
            raise
        if ls_run is not None:
            ls_run.end(outputs={"result": str(result)[:2000]})
        ls_cm.__exit__(None, None, None)
        return result


def mcp_langsmith_interceptors() -> list[McpLangSmithInterceptor]:
    return [McpLangSmithInterceptor()]


def traceable_if_enabled(name: str, *, run_type: str = "chain"):
    def decorator(fn):
        traced: Any = None

        async def wrapper(*args, **kwargs):
            nonlocal traced
            if not tracing_active():
                return await fn(*args, **kwargs)
            if traced is None:
                try:
                    from langsmith import traceable
                except ImportError:
                    return await fn(*args, **kwargs)
                traced = traceable(name=name, run_type=run_type)(fn)
            return await traced(*args, **kwargs)

        wrapper.__name__ = getattr(fn, "__name__", "wrapper")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator