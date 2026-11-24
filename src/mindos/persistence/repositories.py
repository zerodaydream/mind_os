"""Repositories for artifacts and provider usage."""

from __future__ import annotations

from mindos.domain import ArtifactSlice, IdFactory, Usage, current
from mindos.persistence.db import Pool


class ArtifactRepository:
    """Stores oversized tool results. IDs are unguessable ULIDs and never reused."""

    def __init__(self, pool: Pool, new_id: IdFactory) -> None:
        self._pool = pool
        self._new_id = new_id

    async def put(self, content: str, *, media_type: str = "text/plain") -> str:
        artifact_id = self._new_id("art")
        text = content.replace("\x00", "\ufffd")  # PostgreSQL text cannot hold NUL
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO artifacts (id, run_id, media_type, content, size) "
                "VALUES (%s, %s, %s, %s, %s)",
                (artifact_id, current().get("run_id"), media_type, text, len(text)),
            )
        return artifact_id

    async def read(self, artifact_id: str, *, offset: int, limit: int) -> ArtifactSlice | None:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT substr(content, %s, %s) AS text, length(content) AS total "
                "FROM artifacts WHERE id = %s",
                (offset + 1, limit, artifact_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return ArtifactSlice(text=row["text"], offset=offset, total=int(row["total"]))


class UsageRepository:
    """One `provider_usage` row per model call. Reasoning tokens are stored separately."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def record(
        self,
        *,
        role: str,
        model: str,
        usage: Usage,
        latency_ms: int | None,
        cost_usd: float | None,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "INSERT INTO provider_usage (run_id, role, model, input_tokens, output_tokens, "
                "cached_tokens, reasoning_tokens, latency_ms, cost_usd) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    current().get("run_id"),
                    role,
                    model,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.cached_tokens,
                    usage.reasoning_tokens,
                    latency_ms,
                    cost_usd,
                ),
            )
