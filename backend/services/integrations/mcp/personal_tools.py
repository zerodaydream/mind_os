"""In-process MCP pack: Gmail, calendar, search, commitments (same session as the agent)."""

from __future__ import annotations

from collections.abc import Callable

from backend.common.config_loader import get_config
from backend.common.database.models import Commitment
from backend.common.langsmith_setup import trace_tool
from backend.services.integrations.core.calendar.events import (
    create_calendar_event,
    delete_calendar_event,
    delete_calendar_events,
    format_calendar_list,
    format_event_lines,
    format_when_label,
    google_calendar_auth_status,
    list_calendar_events,
    list_calendars,
    query_free_busy,
    split_event_ids,
    update_calendar_event,
)
from backend.services.integrations.core.calendar.scheduling import (
    resolve_event_start,
    validate_create_schedule,
)
from backend.services.integrations.core.calendar.timezone import effective_calendar_tz
from backend.services.integrations.core.gmail.sync import sync_gmail_inbox
from backend.services.integrations.mcp.summarize_url import summarize_public_url
from backend.services.retrieval.core.hybrid import hybrid_search_artifacts
from backend.worker.consumer import drain_outbox
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession


def _oauth_start_url(settings) -> str:
    base = (settings.google_oauth_redirect_uri or "").rsplit("/callback", 1)[0]
    return f"{base}/start" if base else "/v1/integrations/google/start"


def _event_start_from_api(event: dict) -> str:
    start = event.get("start") or {}
    return start.get("dateTime") or start.get("date") or ""


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


def build_personal_tools(
    session: AsyncSession,
    user,
    settings,
    *,
    default_tz: str = "UTC",
    user_task: str = "",
    gateway=None,
    on_partial: Callable[..., None] | None = None,
):
    async def mindos_search(query: str, source: str = "") -> str:
        """Hybrid keyword + vector search over Gmail and notes. Optional source: gmail or manual."""
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
            preview = (h.get("snippet") or "").strip().replace("\n", " ")
            if not preview:
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

    async def mindos_summarize_url(url: str, focus: str = "") -> str:
        """Read a public link or PDF (full document) and return one summary. Use for summarize-this-URL requests."""
        return await summarize_public_url(url, focus, gateway=gateway, on_ping=on_partial)

    async def mindos_google_integration_status() -> str:
        """Whether Google is linked for Gmail/calendar and the sign-in URL if not."""
        if not get_config().is_enabled("integrations"):
            return "Integrations disabled in config.json."
        status = await google_calendar_auth_status(session, user)
        start = _oauth_start_url(settings)
        if status == "ok":
            return "Google connected (calendar scope ok)."
        if status == "missing_scope":
            return f"Google linked but calendar scope missing. Reconnect: {start}"
        return f"Google not connected. Sign in: {start}"

    def _event_line(event: dict, title: str, start_iso: str) -> str:
        summary = event.get("summary") or title
        start_obj = event.get("start") or {}
        evt_tz = str(start_obj.get("timeZone") or default_tz)
        start = format_when_label(
            _event_start_from_api(event) or start_iso,
            evt_tz,
        )
        link = event.get("htmlLink") or ""
        eid = event.get("id") or ""
        base = f"{summary} @ {start}"
        if eid:
            base += f" (id={eid})"
        if link:
            base += f" Link: {link}"
        return base

    async def mindos_calendar_create_event(
        title: str,
        start_iso: str,
        end_iso: str = "",
        description: str = "",
        location: str = "",
        time_zone: str = "",
        calendar_id: str = "primary",
        all_day: bool = False,
        recurrence: str = "",
        attendees: str = "",
        reminders_minutes: int = -1,
    ) -> str:
        """Create a calendar event. all_day + YYYY-MM-DD for all-day. recurrence: RRULE or FREQ=DAILY.
        attendees: comma emails (sends invite). reminders_minutes: popup minutes before (-1 = default)."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        tz = effective_calendar_tz(time_zone, default_tz)
        coerced = resolve_event_start(user_task, tz)
        if coerced:
            start_iso = coerced
        block = validate_create_schedule(
            task_message=user_task,
            start_iso=start_iso,
            all_day=all_day,
            tz=tz,
        )
        if block:
            return f"Calendar create blocked: {block}"
        remind = None if int(reminders_minutes) < 0 else int(reminders_minutes)
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
                time_zone=tz,
                all_day=all_day,
                recurrence=recurrence,
                attendees=attendees,
                reminders_minutes=remind,
            )
            await session.commit()
        except ValueError as exc:
            return f"Calendar create failed: {exc}"
        except Exception as exc:
            return f"Calendar create error: {exc}"
        return f"Created calendar event: {_event_line(event, title, start_iso)}"

    async def mindos_calendar_list_upcoming(
        calendar_id: str = "primary",
        time_min_iso: str = "",
        time_max_iso: str = "",
        query: str = "",
        max_results: int = 15,
    ) -> str:
        """List upcoming events (default: from now). Optional text query filter."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        try:
            items = await list_calendar_events(
                session,
                user=user,
                settings=settings,
                calendar_id=calendar_id,
                time_min_iso=time_min_iso,
                time_max_iso=time_max_iso,
                query=query,
                max_results=max_results,
            )
        except ValueError as exc:
            return f"Calendar list failed: {exc}"
        except Exception as exc:
            return f"Calendar list error: {exc}"
        return format_event_lines(items, limit=max_results)

    async def mindos_calendar_update_event(
        event_id: str,
        calendar_id: str = "primary",
        title: str = "",
        start_iso: str = "",
        end_iso: str = "",
        description: str = "",
        location: str = "",
        time_zone: str = "UTC",
        all_day: bool = False,
        recurrence: str = "",
        attendees: str = "",
        reminders_minutes: int = -1,
    ) -> str:
        """Patch event by id from list/create output. Pass only fields to change."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        remind = None if int(reminders_minutes) < 0 else int(reminders_minutes)
        try:
            event = await update_calendar_event(
                session,
                user=user,
                settings=settings,
                event_id=event_id,
                calendar_id=calendar_id,
                title=title,
                start_iso=start_iso,
                end_iso=end_iso,
                description=description,
                location=location,
                time_zone=time_zone,
                all_day=all_day,
                recurrence=recurrence,
                attendees=attendees,
                reminders_minutes=remind,
            )
            await session.commit()
        except ValueError as exc:
            return f"Calendar update failed: {exc}"
        except Exception as exc:
            return f"Calendar update error: {exc}"
        return f"Updated calendar event: {_event_line(event, title, start_iso)}"

    async def mindos_calendar_delete_event(
        event_id: str,
        calendar_id: str = "primary",
        confirmed: bool = False,
    ) -> str:
        """Delete event(s) by id. event_id may be comma-separated. confirmed=True required if more than one id."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        ids = split_event_ids(event_id)
        if len(ids) > 1 and not confirmed:
            preview = ", ".join(ids[:8])
            extra = f" (+{len(ids) - 8} more)" if len(ids) > 8 else ""
            return (
                f"Bulk delete ({len(ids)} events): {preview}{extra}. "
                "Ask the user to confirm, then call again with confirmed=True."
            )
        try:
            if len(ids) == 1:
                await delete_calendar_event(
                    session,
                    user=user,
                    settings=settings,
                    event_id=ids[0],
                    calendar_id=calendar_id,
                )
                await session.commit()
                return f"Deleted calendar event id={ids[0]}"
            n = await delete_calendar_events(
                session,
                user=user,
                settings=settings,
                event_ids=ids,
                calendar_id=calendar_id,
            )
            await session.commit()
        except ValueError as exc:
            return f"Calendar delete failed: {exc}"
        except Exception as exc:
            return f"Calendar delete error: {exc}"
        return f"Deleted {n} calendar event(s)."

    async def mindos_calendar_list_calendars() -> str:
        """List writable Google calendars (ids for create/list/free-busy)."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        try:
            items = await list_calendars(session, user=user, settings=settings)
        except ValueError as exc:
            return f"Calendar list failed: {exc}"
        except Exception as exc:
            return f"Calendar list error: {exc}"
        return format_calendar_list(items)

    async def mindos_calendar_free_busy(
        time_min_iso: str,
        time_max_iso: str,
        calendar_ids: str = "primary",
    ) -> str:
        """Free/busy blocks. calendar_ids comma-separated."""
        if not get_config().is_enabled("integrations"):
            return "Calendar disabled (integrations off in config.json)."
        try:
            return await query_free_busy(
                session,
                user=user,
                settings=settings,
                time_min_iso=time_min_iso,
                time_max_iso=time_max_iso,
                calendar_ids=calendar_ids,
            )
        except ValueError as exc:
            return f"Free/busy failed: {exc}"
        except Exception as exc:
            return f"Free/busy error: {exc}"

    return [
        trace_tool(mindos_search),
        trace_tool(mindos_gmail_sync),
        trace_tool(mindos_process_outbox),
        trace_tool(mindos_commitments),
        trace_tool(mindos_summarize_url),
        trace_tool(mindos_google_integration_status),
        trace_tool(mindos_calendar_create_event),
        trace_tool(mindos_calendar_list_upcoming),
        trace_tool(mindos_calendar_update_event),
        trace_tool(mindos_calendar_delete_event),
        trace_tool(mindos_calendar_list_calendars),
        trace_tool(mindos_calendar_free_busy),
    ]