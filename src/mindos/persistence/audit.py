"""Append-only audit repository. The table rejects UPDATE, DELETE, and TRUNCATE (see baseline)."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from mindos.domain import current, redact
from mindos.persistence.db import Pool


class AuditRepository:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def append(
        self,
        *,
        event_type: str,
        actor: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Insert one audit record and return its ID.

        The body is redacted before storage. The run ID comes from the bound correlation context.
        """
        body = redact(data or {})
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                INSERT INTO audit_events (event_type, actor, subject_type, subject_id, run_id, data)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (event_type, actor, subject_type, subject_id, current().get("run_id"), Jsonb(body)),
            )
            row = await cur.fetchone()
        assert row is not None
        return int(row["id"])
