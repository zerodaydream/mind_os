"""Integration tests for approval repository and state machine triggers in PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from opsagent.domain import Principal, RiskClass
from opsagent.persistence import (
    ApprovalRepository,
    open_pool,
    upgrade,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def migrated_db(db_url: str) -> str:
    upgrade(db_url)
    return db_url


async def test_approvals_crud_and_atomic_claim(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        expires = datetime.now(UTC) + timedelta(hours=2)
        await repo.create(
            approval_id="apr_pg_1",
            tool="mail.send_message",
            tool_version="1",
            principal=Principal.AGENT,
            risk=RiskClass.R3,
            payload_hash="hash_abc",
            idempotency_key="idem_abc",
            args_json='{"to":"test@example.com"}',
            summary="Send email",
            payload_preview="to: test@example.com",
            tainted=False,
            expires_at=expires,
            run_id="run_pg_1",
        )

        rec = await repo.get("apr_pg_1")
        assert rec is not None
        assert rec.state == "PENDING"

        # Approve
        ok = await repo.transition(
            "apr_pg_1",
            from_states=frozenset({"PENDING"}),
            to_state="APPROVED",
            actor="USER",
        )
        assert ok is True

        # Concurrent claim: two workers race to claim CONSUMED
        claim1 = await repo.transition(
            "apr_pg_1",
            from_states=frozenset({"APPROVED"}),
            to_state="CONSUMED",
        )
        claim2 = await repo.transition(
            "apr_pg_1",
            from_states=frozenset({"APPROVED"}),
            to_state="CONSUMED",
        )
        assert claim1 is True
        assert claim2 is False  # Second claim must fail atomically!

        # Final transition to EXECUTED
        ok = await repo.transition(
            "apr_pg_1",
            from_states=frozenset({"CONSUMED"}),
            to_state="EXECUTED",
        )
        assert ok is True
        rec_final = await repo.get("apr_pg_1")
        assert rec_final is not None and rec_final.state == "EXECUTED"
    finally:
        await pool.close()


def test_db_trigger_enforces_forward_only_transitions(migrated_db: str) -> None:
    """PostgreSQL trigger must reject illegal jumps even from direct raw SQL."""
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            """
            INSERT INTO approvals (id, tool, tool_version, principal, risk, state,
                                   payload_hash, idempotency_key, args_json, summary,
                                   payload_preview, tainted)
            VALUES ('apr_trig_1', 't', '1', 'AGENT', 'R3', 'PENDING',
                    'h1', 'k1', '{}', 'sum', 'prev', false)
            """
        )
        # PENDING -> APPROVED is allowed
        conn.execute("UPDATE approvals SET state = 'APPROVED' WHERE id = 'apr_trig_1'")

        # APPROVED -> PENDING is illegal backwards jump
        with pytest.raises(psycopg.Error, match="invalid approval state transition"):
            conn.execute("UPDATE approvals SET state = 'PENDING' WHERE id = 'apr_trig_1'")

        # APPROVED -> CONSUMED -> EXECUTED
        conn.execute("UPDATE approvals SET state = 'CONSUMED' WHERE id = 'apr_trig_1'")
        conn.execute("UPDATE approvals SET state = 'EXECUTED' WHERE id = 'apr_trig_1'")

        # EXECUTED is terminal; jumping from EXECUTED to anything raises
        with pytest.raises(psycopg.Error, match="invalid approval state transition"):
            conn.execute("UPDATE approvals SET state = 'CONSUMED' WHERE id = 'apr_trig_1'")


async def test_expire_stale_approvals(migrated_db: str) -> None:
    pool = await open_pool(migrated_db)
    try:
        repo = ApprovalRepository(pool)
        # Create an already expired approval
        past = datetime.now(UTC) - timedelta(minutes=5)
        await repo.create(
            approval_id="apr_stale_1",
            tool="tool.old",
            tool_version="1",
            principal=Principal.AGENT,
            risk=RiskClass.R1,
            payload_hash="h_stale",
            idempotency_key="k_stale",
            args_json="{}",
            summary="Old",
            payload_preview="Old",
            tainted=False,
            expires_at=past,
            run_id="run_stale",
        )
        expired_count = await repo.expire_stale()
        assert expired_count >= 1
        rec = await repo.get("apr_stale_1")
        assert rec is not None and rec.state == "EXPIRED"
    finally:
        await pool.close()
