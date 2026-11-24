"""Approval repository: state-machine transitions are atomic SQL, never Python conditionals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mindos.domain import ApprovalRecord, Principal, RiskClass
from mindos.persistence.db import Pool

# Every transition stamps `resolved_at`; `actor` keeps who last made a human decision, so the
# engine's own transitions (CONSUMED, EXECUTED) never erase who approved.
_TRANSITION = """
    UPDATE approvals
    SET state = %(to_state)s,
        resolved_at = now(),
        actor = COALESCE(%(actor)s, actor),
        original_payload_hash = CASE
            WHEN %(payload_hash)s::text IS NOT NULL AND original_payload_hash IS NULL
            THEN payload_hash ELSE original_payload_hash END,
        payload_hash = COALESCE(%(payload_hash)s, payload_hash),
        args_json = COALESCE(%(args_json)s, args_json)
    WHERE id = %(id)s
      AND state = ANY(%(from_states)s)
      AND (NOT %(require_unexpired)s OR expires_at IS NULL OR expires_at > now())
    RETURNING id
"""


class ApprovalRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def create(
        self,
        *,
        approval_id: str,
        tool: str,
        tool_version: str,
        principal: Principal,
        risk: RiskClass,
        payload_hash: str,
        idempotency_key: str,
        args_json: str,
        summary: str,
        payload_preview: str,
        tainted: bool,
        expires_at: datetime,
        run_id: str | None,
        thread_id: str | None = None,
        conversation_id: str | None = None,
        tool_call_id: str | None = None,
        agent: str | None = None,
        deferred: bool = False,
    ) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO approvals
                  (id, run_id, tool, tool_version, principal, risk, state,
                   payload_hash, idempotency_key, args_json, summary, payload_preview,
                   tainted, expires_at, thread_id, conversation_id, tool_call_id,
                   agent, deferred)
                VALUES (%s,%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (
                    approval_id,
                    run_id,
                    tool,
                    tool_version,
                    principal.value,
                    risk.value,
                    payload_hash,
                    idempotency_key,
                    args_json,
                    summary,
                    payload_preview,
                    tainted,
                    expires_at,
                    thread_id,
                    conversation_id,
                    tool_call_id,
                    agent,
                    deferred,
                ),
            )
            return await cur.fetchone() is not None

    async def transition(
        self,
        approval_id: str,
        *,
        from_states: frozenset[str],
        to_state: str,
        actor: str | None = None,
        payload_hash: str | None = None,
        args_json: str | None = None,
        require_unexpired: bool = False,
    ) -> bool:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                _TRANSITION,
                {
                    "to_state": to_state,
                    "actor": actor,
                    "payload_hash": payload_hash,
                    "args_json": args_json,
                    "id": approval_id,
                    "from_states": sorted(from_states),
                    "require_unexpired": require_unexpired,
                },
            )
            return await cur.fetchone() is not None

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        return await self._one("SELECT * FROM approvals WHERE id = %s", (approval_id,))

    async def get_by_idempotency_key(self, key: str) -> ApprovalRecord | None:
        return await self._one("SELECT * FROM approvals WHERE idempotency_key = %s", (key,))

    async def expire_stale(self) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE approvals
                SET state = 'EXPIRED', resolved_at = now()
                WHERE state IN ('PENDING','APPROVED','APPROVED_EDITED')
                  AND expires_at IS NOT NULL AND expires_at <= now()
                RETURNING *
                """
            )
            rows = await cur.fetchall()
        return [_row_to_record(dict(r)) for r in rows]

    async def abandon_consumed(self) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE approvals
                SET state = 'EXEC_FAILED', resolved_at = now(), actor = 'RECONCILER'
                WHERE state = 'CONSUMED'
                RETURNING *
                """
            )
            rows = await cur.fetchall()
        return [_row_to_record(dict(r)) for r in rows]

    async def undelivered(self) -> list[ApprovalRecord]:
        """Rows whose paused graph may still be waiting for the outcome, in any state.

        Whether a graph is really still paused is the checkpoint's answer, not the table's: a
        crash after the effect but before the checkpoint leaves an EXECUTED row whose graph is
        still waiting. The runtime treats re-delivery to a graph that is not paused as a no-op.
        """
        return await self._many(
            """
            SELECT * FROM approvals
            WHERE delivered_at IS NULL AND thread_id IS NOT NULL
            ORDER BY created_at
            """,
            (),
        )

    async def mark_delivered(self, approval_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE approvals SET delivered_at = now() WHERE id = %s AND delivered_at IS NULL",
                (approval_id,),
            )

    async def _one(self, sql: str, params: tuple[Any, ...]) -> ApprovalRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            row = await cur.fetchone()
        return None if row is None else _row_to_record(dict(row))

    async def _many(self, sql: str, params: tuple[Any, ...]) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(sql, params)
            rows = await cur.fetchall()
        return [_row_to_record(dict(r)) for r in rows]


def _row_to_record(row: dict[str, Any]) -> ApprovalRecord:
    return ApprovalRecord(
        id=row["id"],
        run_id=row.get("run_id"),
        tool=row["tool"],
        tool_version=row["tool_version"],
        principal=Principal(row["principal"]),
        risk=RiskClass(row["risk"]),
        state=row["state"],
        payload_hash=row["payload_hash"],
        idempotency_key=row["idempotency_key"],
        args_json=row["args_json"],
        summary=row["summary"],
        payload_preview=row["payload_preview"],
        tainted=bool(row.get("tainted", False)),
        expires_at=row["expires_at"].isoformat() if row.get("expires_at") else None,
        actor=row.get("actor"),
        thread_id=row.get("thread_id"),
        conversation_id=row.get("conversation_id"),
        tool_call_id=row.get("tool_call_id"),
        original_payload_hash=row.get("original_payload_hash"),
        delivered=row.get("delivered_at") is not None,
        agent=row.get("agent"),
        deferred=bool(row.get("deferred", False)),
    )
