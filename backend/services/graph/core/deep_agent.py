"""Deep Agents (LangGraph) for plan + execute intents."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.database.models import Commitment
from backend.common.utils.enums import LlmMode
from backend.services.agents.core.markdown_tables import normalize_markdown_tables
from backend.services.agents.core.prompt_budget import DEEP_USER_CONTEXT_MAX, clip_text
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts

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


def _build_tools(session: AsyncSession, user, settings):
    async def mindos_search(query: str) -> str:
        """Search indexed MindOS artifacts (Gmail, notes) by keyword. Returns titles and sources."""
        hits = await hybrid_search_artifacts(
            session, user_id=user.id, query=query, settings=settings, limit=6
        )
        if not hits:
            return "No matching artifacts."
        return "\n".join(
            f"- [{h.get('source')}] {h.get('title') or h['artifact_id']}" for h in hits
        )

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

    return [mindos_search, mindos_commitments]


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
            "MindOS deep agent: use prefetched context first, then mindos_search for gaps. "
            "Tables/lists only from retrieved data. Be concise and factual; finish every sentence. "
            "Use write_todos for multi-step work. Stay in workspace root."
        ),
    }
    if intent == "execute":
        from deepagents.backends import FilesystemBackend

        kwargs["backend"] = FilesystemBackend(root_dir=str(_fs_root()), virtual_mode=True)

    checkpointer = _checkpointer()
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer

    agent = create_deep_agent(**kwargs)
    config = {"configurable": {"thread_id": str(thread_id)}}
    user_content = message
    if turn_context is not None and turn_context.has_context:
        ctx_block = clip_text(turn_context.context_text, DEEP_USER_CONTEXT_MAX)
        user_content = (
            f"Preloaded context:\n{ctx_block}\n\n"
            f"User task:\n{message}"
        )
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_content}]},
            config=config,
        )
    except Exception as exc:
        return {
            "answer": f"Deep agent failed: {exc}",
            "mode": "deep_error",
            "intent": intent,
        }

    answer = normalize_markdown_tables(_extract_answer(result) or "")
    sources = turn_context.sources if turn_context else []
    return {
        "answer": answer or "(empty agent reply)",
        "mode": "deep_agent",
        "intent": intent,
        "sources": sources,
    }


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
                text = "".join(parts).strip()
                if text:
                    return text
    return clip_text(str(result), 12_000, min_keep=4000)
