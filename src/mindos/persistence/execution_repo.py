"""`tool_executions`: durable evidence that a side effect started, before the call is made."""

from __future__ import annotations

from opsagent.domain import Principal, RiskClass
from opsagent.persistence.db import Pool


class ExecutionRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def start(
        self,
        *,
        execution_id: str,
        run_id: str | None,
        tool: str,
        agent: str,
        principal: Principal,
        risk: RiskClass,
        idempotency_key: str,
        args_hash: str,
    ) -> bool:
        """Insert STARTED. The unique key makes a replayed call refuse to run a second time."""
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO tool_executions
                  (id, run_id, tool, agent, principal, risk, state, idempotency_key, args_hash)
                VALUES (%s,%s,%s,%s,%s,%s,'STARTED',%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """,
                (
                    execution_id,
                    run_id,
                    tool,
                    agent,
                    principal.value,
                    risk.value,
                    idempotency_key,
                    args_hash,
                ),
            )
            return await cur.fetchone() is not None

    async def finish(self, execution_id: str, *, ok: bool) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE tool_executions
                SET state = %s, finished_at = now()
                WHERE id = %s AND state = 'STARTED'
                """,
                ("FINISHED" if ok else "FAILED", execution_id),
            )

    async def abandon_started(self) -> list[tuple[str, str, str | None]]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                UPDATE tool_executions
                SET state = 'UNKNOWN', finished_at = now()
                WHERE state = 'STARTED'
                RETURNING id, tool, run_id
                """
            )
            rows = await cur.fetchall()
        return [(str(r["id"]), str(r["tool"]), r["run_id"]) for r in rows]
