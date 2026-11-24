"""Approvals on real PostgreSQL: atomic claims, the trigger, expiry, and reconciliation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from opsagent.application import ApprovalBroker
from opsagent.domain import (
    ALLOWED_TRANSITIONS,
    ApprovalRecord,
    ApprovalState,
    Principal,
    RiskClass,
    bind,
)
from opsagent.infra import SystemClock, new_id
from opsagent.persistence import (
    ApprovalRepository,
    AuditRepository,
    ExecutionRepository,
    open_pool,
    upgrade,
)

pytestmark = pytest.mark.integration

ALL_STATES: tuple[ApprovalState, ...] = tuple(ALLOWED_TRANSITIONS)


@pytest.fixture
def migrated_db(db_url: str) -> str:
    upgrade(db_url)
    return db_url


def _insert(
    conn: psycopg.Connection[Any], approval_id: str, state: str = "PENDING", **kw: Any
) -> None:
    conn.execute(
        """
        INSERT INTO approvals (id, tool, tool_version, principal, risk, state, payload_hash,
                               idempotency_key, args_json, summary, payload_preview, expires_at,
                               thread_id, conversation_id, tool_call_id, run_id)
        VALUES (%s, 't', '1', 'AGENT', 'R3', %s, 'h1', %s, '{"a":1}', 's', 'p', %s,
                'thr', 'conv', 'call', 'run')
        """,
        (approval_id, state, f"key_{approval_id}", kw.get("expires_at")),
    )


async def _create(repo: ApprovalRepository, approval_id: str = "apr_1", **kw: Any) -> bool:
    return await repo.create(
        **{
            "approval_id": approval_id,
            "tool": "mail.send_message",
            "tool_version": "1",
            "principal": Principal.AGENT,
            "risk": RiskClass.R3,
            "payload_hash": "hash_abc",
            "idempotency_key": f"idem_{approval_id}",
            "args_json": '{"to":"test@example.com"}',
            "summary": "Send email",
            "payload_preview": "to: test@example.com",
            "tainted": False,
            "expires_at": datetime.now(UTC) + timedelta(hours=2),
            "run_id": "run_1",
            "thread_id": "thr_1",
            "conversation_id": "conv_1",
            "tool_call_id": "call_1",
            **kw,
        }
    )


# ---- the trigger -----------------------------------------------------------------------------
def test_db_trigger_enforces_forward_only_transitions(migrated_db: str) -> None:
    """Raw SQL cannot take an approval backwards or out of a terminal state."""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _insert(conn, "apr_trig_1")
        conn.execute("UPDATE approvals SET state = 'APPROVED' WHERE id = 'apr_trig_1'")
        with pytest.raises(psycopg.Error, match="invalid approval state transition"):
            conn.execute("UPDATE approvals SET state = 'PENDING' WHERE id = 'apr_trig_1'")
        conn.execute("UPDATE approvals SET state = 'CONSUMED' WHERE id = 'apr_trig_1'")
        conn.execute("UPDATE approvals SET state = 'EXECUTED' WHERE id = 'apr_trig_1'")
        with pytest.raises(psycopg.Error, match="invalid approval state transition"):
            conn.execute("UPDATE approvals SET state = 'CONSUMED' WHERE id = 'apr_trig_1'")


def test_the_python_state_machine_and_the_sql_trigger_agree_on_every_pair(migrated_db: str) -> None:
    """If these ever drift, one of them is wrong. Checked for all 10 x 10 pairs."""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        for i, src in enumerate(ALL_STATES):
            for j, dst in enumerate(ALL_STATES):
                if src == dst:
                    continue
                approval_id = f"apr_{i}_{j}"
                _insert(conn, approval_id, src)
                allowed = dst in ALLOWED_TRANSITIONS[src]
                try:
                    conn.execute(
                        "UPDATE approvals SET state = %s WHERE id = %s", (dst, approval_id)
                    )
                    accepted = True
                except psycopg.Error:
                    accepted = False
                assert accepted is allowed, f"{src} -> {dst}: trigger {accepted}, domain {allowed}"


def test_the_approved_payload_is_immutable_except_for_the_users_edit(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _insert(conn, "apr_imm")
        conn.execute("UPDATE approvals SET state = 'APPROVED' WHERE id = 'apr_imm'")
        for column, value in (("payload_hash", "evil"), ("args_json", '{"a":2}')):
            with pytest.raises(psycopg.Error, match="payload is immutable"):
                conn.execute(f"UPDATE approvals SET {column} = %s WHERE id = 'apr_imm'", (value,))
        with pytest.raises(psycopg.Error, match="identity columns are immutable"):
            conn.execute("UPDATE approvals SET tool = 'files.delete' WHERE id = 'apr_imm'")

        _insert(conn, "apr_edit")  # the legal change: PENDING -> APPROVED_EDITED re-hashes
        conn.execute(
            "UPDATE approvals SET state = 'APPROVED_EDITED', payload_hash = 'h2' "
            "WHERE id = 'apr_edit'"
        )


def test_an_expired_approval_cannot_be_approved_or_consumed_even_by_raw_sql(
    migrated_db: str,
) -> None:
    past = datetime.now(UTC) - timedelta(minutes=1)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        _insert(conn, "apr_old", "PENDING", expires_at=past)
        with pytest.raises(psycopg.Error, match="approval expired"):
            conn.execute("UPDATE approvals SET state = 'APPROVED' WHERE id = 'apr_old'")
        _insert(conn, "apr_old2", "APPROVED", expires_at=past)
        with pytest.raises(psycopg.Error, match="approval expired"):
            conn.execute("UPDATE approvals SET state = 'CONSUMED' WHERE id = 'apr_old2'")
        conn.execute(
            "UPDATE approvals SET state = 'EXPIRED' WHERE id = 'apr_old2'"
        )  # expiring is fine


# ---- the repository ---------------------------------------------------------------------------
async def test_create_is_idempotent_and_get_by_key_finds_the_original(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        assert await _create(repo, "apr_a", idempotency_key="same") is True
        assert await _create(repo, "apr_b", idempotency_key="same") is False  # a replay
        found = await repo.get_by_idempotency_key("same")
        assert found is not None and found.id == "apr_a"
        assert found.thread_id == "thr_1" and found.tool_call_id == "call_1"
        assert await repo.get("apr_b") is None
    finally:
        await pool.close()


async def test_twenty_racing_claims_produce_exactly_one_winner(migrated_db: str) -> None:
    pool = await open_pool(migrated_db, max_size=20)
    try:
        repo = ApprovalRepository(pool)
        await _create(repo)
        await repo.transition(
            "apr_1", from_states=frozenset({"PENDING"}), to_state="APPROVED", actor="USER"
        )
        claims = await asyncio.gather(
            *(
                repo.transition(
                    "apr_1",
                    from_states=frozenset({"APPROVED", "APPROVED_EDITED"}),
                    to_state="CONSUMED",
                    require_unexpired=True,
                )
                for _ in range(20)
            )
        )
        assert claims.count(True) == 1
        rec = await repo.get("apr_1")
        assert rec is not None and rec.state == "CONSUMED"
        assert rec.actor == "USER"  # the engine's transition must not erase who approved
    finally:
        await pool.close()


async def test_expiry_is_part_of_the_atomic_statement(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        await _create(repo, expires_at=datetime.now(UTC) - timedelta(seconds=1))
        for to_state in ("APPROVED", "APPROVED_EDITED"):
            ok = await repo.transition(
                "apr_1",
                from_states=frozenset({"PENDING"}),
                to_state=to_state,
                require_unexpired=True,
            )
            assert ok is False
        rec = await repo.get("apr_1")
        assert rec is not None and rec.state == "PENDING"  # untouched; the sweep owns EXPIRED
        swept = await repo.expire_stale()
        assert [r.id for r in swept] == ["apr_1"]
        after = await repo.get("apr_1")
        assert after is not None and after.state == "EXPIRED"
    finally:
        await pool.close()


async def test_an_edit_keeps_the_original_hash_and_swaps_the_payload(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        await _create(repo)
        ok = await repo.transition(
            "apr_1",
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED_EDITED",
            actor="USER",
            payload_hash="hash_new",
            args_json='{"to":"other@example.com"}',
        )
        rec = await repo.get("apr_1")
        assert ok and rec is not None
        assert (rec.payload_hash, rec.original_payload_hash) == ("hash_new", "hash_abc")
        assert rec.args_json == '{"to":"other@example.com"}'
    finally:
        await pool.close()


async def test_reconciliation_worklist_delivery_and_unknown_outcomes(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        await _create(repo, "apr_pending")
        await _create(repo, "apr_consumed")
        await repo.transition(
            "apr_consumed", from_states=frozenset({"PENDING"}), to_state="APPROVED", actor="USER"
        )
        await repo.transition(
            "apr_consumed", from_states=frozenset({"APPROVED"}), to_state="CONSUMED"
        )
        assert {r.id for r in await repo.undelivered()} == {"apr_pending", "apr_consumed"}
        await repo.mark_delivered("apr_pending")
        assert {r.id for r in await repo.undelivered()} == {"apr_consumed"}

        stuck = await repo.abandon_consumed()
        assert [r.id for r in stuck] == ["apr_consumed"]
        rec = await repo.get("apr_consumed")
        assert rec is not None and (rec.state, rec.actor) == ("EXEC_FAILED", "RECONCILER")
    finally:
        await pool.close()


async def test_execution_log_refuses_a_duplicate_and_marks_in_flight_rows_unknown(
    migrated_db: str,
) -> None:
    pool = await open_pool(migrated_db)
    try:
        log = ExecutionRepository(pool)

        async def start(execution_id: str, key: str) -> bool:
            return await log.start(
                execution_id=execution_id,
                run_id="run_1",
                tool="mail.send_message",
                agent="main",
                principal=Principal.AGENT,
                risk=RiskClass.R3,
                idempotency_key=key,
                args_hash="h",
            )

        assert await start("exe_1", "k1") is True
        assert await start("exe_dup", "k1") is False  # same call: must not run twice
        assert await start("exe_2", "k2") is True
        await log.finish("exe_1", ok=True)
        assert await log.abandon_started() == [("exe_2", "mail.send_message", "run_1")]
        assert await log.abandon_started() == []
    finally:
        await pool.close()


async def test_broker_on_postgres_end_to_end_with_audit(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        broker = ApprovalBroker(
            repo=ApprovalRepository(pool),
            audit=AuditRepository(pool),
            new_id=new_id,
            clock=SystemClock(),
        )
        with bind(run_id="run_9", conversation_id="conv_9", thread_id="thr_9"):
            ticket = await broker.request(
                tool="mail.send_message",
                tool_version="1",
                principal=Principal.AGENT,
                risk=RiskClass.R3,
                args={"to": "a@b.c"},
                run_id="run_9",
                tool_call_id="call_9",
                tainted=True,
            )
            again = await broker.request(
                tool="mail.send_message",
                tool_version="1",
                principal=Principal.AGENT,
                risk=RiskClass.R3,
                args={"to": "a@b.c"},
                run_id="run_9",
                tool_call_id="call_9",
                tainted=True,
            )
        assert again.view.approval_id == ticket.view.approval_id and not again.created
        aid = ticket.view.approval_id
        assert (await broker.resolve(aid, decision="approve")).outcome == "approved"
        assert await broker.consume(aid) is True and await broker.consume(aid) is False
        await broker.mark_executed(aid, success=True)
        rec: ApprovalRecord | None = await broker.get(aid)
        assert rec is not None and rec.state == "EXECUTED" and rec.actor == "USER"
    finally:
        await pool.close()
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT event_type FROM audit_events WHERE subject_id = %s ORDER BY id", (aid,)
        ).fetchall()
    assert [r[0] for r in rows] == [
        "approval.created",
        "approval.approved",
        "approval.consumed",
        "approval.executed",
    ]
