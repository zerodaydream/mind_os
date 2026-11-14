"""Deep Agents (LangGraph) for plan + execute intents."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.common.config_loader import get_config
from backend.common.database.models import Commitment
from backend.common.utils.enums import LlmMode
from backend.services.agents.core.markdown_tables import normalize_markdown_tables
from backend.services.agents.core.prompt_budget import DEEP_USER_CONTEXT_MAX, clip_text
from backend.services.agents.core.turn_context import preflight_prompt_block
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

    return [mindos_search, mindos_gmail_sync, mindos_process_outbox, mindos_commitments]


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
            "MindOS deep agent: answer from Preloaded context and tool results only; never invent mail. "
            "Before calling mindos_gmail_sync or mindos_process_outbox, send the user one short sentence "
            "about what you are about to do; then call the tool. "
            "Use mindos_search for gaps. For mail tasks with no hits: mindos_gmail_sync → "
            "mindos_process_outbox if ingested > 0 → mindos_search (at most one extra sync cycle). "
            "If Steps already taken were completed before you started, summarize them in your opening. "
            "Tables/lists only from retrieved data. Be concise; finish every sentence. "
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
        pre = preflight_prompt_block(turn_context.preflight_actions)
        user_content = f"Preloaded context:\n{ctx_block}"
        if pre:
            user_content += f"\n\n{pre}"
        user_content += f"\n\nUser task:\n{message}"
    elif turn_context is not None and turn_context.preflight_actions:
        pre = preflight_prompt_block(turn_context.preflight_actions)
        user_content = f"{pre}\n\nUser task:\n{message}"
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
                joined = "".join(parts).strip()
                if joined:
                    return joined
    return clip_text(str(result), 12_000, min_keep=4000)