from __future__ import annotations

from datetime import timedelta

import psycopg
import pytest

from opsagent.domain import Usage, bind, events
from opsagent.infra import new_id
from opsagent.persistence import (
    ArtifactRepository,
    AuditRepository,
    UsageRepository,
    open_checkpointer,
    open_pool,
    upgrade,
)
from tests.support.agent_kit import MONDAY_0930, make_kit, standup
from tests.support.scripted_model import ScriptedChatModel, reply, tool_call

pytestmark = pytest.mark.integration


def _rows(url: str, sql: str) -> list[tuple[object, ...]]:
    with psycopg.connect(url) as conn:
        return conn.execute(sql).fetchall()


async def test_artifact_repository_stores_and_slices(db_url: str) -> None:
    upgrade(db_url)
    pool = await open_pool(db_url)
    try:
        repo = ArtifactRepository(pool, new_id)
        with bind(run_id="run_x"):
            artifact_id = await repo.put("héllo\x00wörld " * 100)
        assert artifact_id.startswith("art_")
        piece = await repo.read(artifact_id, offset=6, limit=5)
        assert piece is not None and piece.text == "wörld" and piece.offset == 6
        assert piece.total == len("héllo\ufffdwörld ") * 100
        assert piece.next_offset == 11
        tail = await repo.read(artifact_id, offset=piece.total - 3, limit=50)
        assert tail is not None and tail.next_offset is None
        assert await repo.read("art_missing", offset=0, limit=10) is None
    finally:
        await pool.close()
    assert _rows(db_url, "SELECT run_id, media_type FROM artifacts") == [("run_x", "text/plain")]


async def test_usage_repository_keeps_reasoning_tokens_separate(db_url: str) -> None:
    upgrade(db_url)
    pool = await open_pool(db_url)
    try:
        with bind(run_id="run_u"):
            await UsageRepository(pool).record(
                role="orchestrator",
                model="m",
                usage=Usage(input_tokens=10, output_tokens=7, cached_tokens=2, reasoning_tokens=4),
                latency_ms=None,
                cost_usd=0.000123,
            )
    finally:
        await pool.close()
    row = _rows(
        db_url,
        "SELECT run_id, input_tokens, output_tokens, cached_tokens, reasoning_tokens, cost_usd "
        "FROM provider_usage",
    )[0]
    assert row[:5] == ("run_u", 10, 7, 2, 4) and float(row[5]) == pytest.approx(0.000123)  # type: ignore[arg-type]


async def test_full_run_on_postgres_checkpoints_artifacts_audit_and_usage(db_url: str) -> None:
    """Real checkpointer with the restricted serializer, real repositories, scripted models."""
    upgrade(db_url)
    pool = await open_pool(db_url)
    many = [
        standup().model_copy(update={"id": f"e{i}", "start": MONDAY_0930 + timedelta(minutes=i)})
        for i in range(60)
    ]
    orchestrator = ScriptedChatModel(
        script=[
            reply(
                "Looking.",
                thinking="plan",
                calls=[tool_call("delegate", {"subagent": "calendar", "goal": "list all"}, "d1")],
            ),
            reply("Done."),
        ]
    )
    subagent = ScriptedChatModel(
        script=[
            reply(
                calls=[
                    tool_call(
                        "calendar.search_events",
                        {"start": "2026-11-23", "end": "2026-11-23", "limit": 50},
                        "c1",
                    )
                ]
            ),
            reply("Listed 50 events."),
        ]
    )
    try:
        async with open_checkpointer(db_url) as saver:
            kit = make_kit(
                orchestrator,
                subagent,
                events=many,
                saver=saver,
                audit=AuditRepository(pool),
                artifacts=ArtifactRepository(pool, new_id),
                usage=UsageRepository(pool),
            )
            first = await kit.collect(kit.spec("list my day"))
            assert isinstance(first[-1], events.UsageUpdated | events.AssistantMessageComplete)
            orchestrator.load(reply("Second turn."))
            await kit.collect(kit.spec("again"))  # same thread: reads the checkpoint back
            history = " ".join(str(m.content) for m in orchestrator.seen[-1])
            assert "list my day" in history and "Done." in history
    finally:
        await pool.close()
    audit = _rows(db_url, "SELECT event_type, run_id FROM audit_events ORDER BY id")
    assert [a[0] for a in audit] == ["tool.executed", "tool.executed"]
    assert {a[1] for a in audit} == {"run_1"}
    assert _rows(db_url, "SELECT count(*) FROM provider_usage")[0][0] >= 3
    assert _rows(db_url, "SELECT count(*) FROM artifacts")[0][0] == 1  # the oversized listing
    assert _rows(db_url, "SELECT count(*) FROM checkpoints")[0][0] > 0
