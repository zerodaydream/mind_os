"""Approval repository: state-machine transitions are atomic SQL, never Python conditionals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from opsagent.domain import Principal, RiskClass
from opsagent.domain.approvals import ApprovalRecord
from opsagent.persistence.db import Pool


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
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO approvals
                  (id, run_id, tool, tool_version, principal, risk, state,
                   payload_hash, idempotency_key, args_json, summary, payload_preview,
                   tainted, expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,'PENDING',%s,%s,%s,%s,%s,%s,%s)
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
                ),
            )

    async def transition(
        self,
        approval_id: str,
        *,
        from_states: frozenset[str],
        to_state: str,
        actor: str | None = None,
        payload_hash: str | None = None,
        args_json: str | None = None,
    ) -> bool:
        """Atomically transition state. Returns True if exactly one row was updated."""
        from_list = list(from_states)
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE approvals
                SET state = %s, resolved_at = now(), actor = %s,
                    payload_hash = COALESCE(%s, payload_hash),
                    args_json = COALESCE(%s, args_json)
                WHERE id = %s AND state = ANY(%s)
                RETURNING id
                """,
                (to_state, actor, payload_hash, args_json, approval_id, from_list),
            )
            row = await cur.fetchone()
        return row is not None

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM approvals WHERE id = %s", (approval_id,)
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return _row_to_record(dict(row))

    async def pending_for_run(self, run_id: str) -> list[ApprovalRecord]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM approvals WHERE run_id = %s AND state = 'PENDING'", (run_id,)
            )
            rows = await cur.fetchall()
        return [_row_to_record(dict(r)) for r in rows]

    async def expire_stale(self) -> int:
        """Set PENDING+APPROVED+APPROVED_EDITED rows past expires_at to EXPIRED. Returns count."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE approvals
                SET state = 'EXPIRED', resolved_at = now()
                WHERE state IN ('PENDING','APPROVED','APPROVED_EDITED')
                  AND expires_at < now()
                RETURNING id
                """
            )
            rows = await cur.fetchall()
        return len(rows)

    async def find_started_executions(self) -> list[tuple[str, str, str | None]]:
        """Find tool_executions left in STARTED state across restarts."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, tool, run_id FROM tool_executions WHERE state = 'STARTED'"
            )
            rows = await cur.fetchall()
        return [(str(r["id"]), str(r["tool"]), r.get("run_id")) for r in rows]


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
    )
