"""Approvals: state machine tables, approval_batches, and indexes.

Forward-only. All approval state lives here; tool_executions tracks pipeline progress.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_STATEMENTS = (
    # tool_executions: pipeline progress per invocation
    """
    CREATE TABLE tool_executions (
        id              text PRIMARY KEY,
        run_id          text,
        tool            text,
        agent           text,
        principal       text,
        risk            text,
        state           text NOT NULL
                            CHECK (state IN ('STARTED','FINISHED','FAILED','AWAITING_AUTH')),
        idempotency_key text UNIQUE,
        args_hash       text,
        started_at      timestamptz NOT NULL DEFAULT now(),
        finished_at     timestamptz
    )
    """,
    "CREATE INDEX tool_executions_run_idx ON tool_executions (run_id)",
    # approvals: one row per user decision request
    """
    CREATE TABLE approvals (
        id              text PRIMARY KEY,
        run_id          text,
        tool            text,
        tool_version    text,
        principal       text,
        risk            text,
        state           text NOT NULL
                            CHECK (state IN (
                                'PENDING','APPROVED','APPROVED_EDITED','REJECTED',
                                'EXPIRED','CANCELLED','CONSUMED','EXECUTED',
                                'EXEC_FAILED','INVALIDATED'
                            )),
        payload_hash    text NOT NULL,
        idempotency_key text NOT NULL UNIQUE,
        args_json       text NOT NULL,
        summary         text NOT NULL,
        payload_preview text NOT NULL,
        tainted         bool NOT NULL DEFAULT false,
        expires_at      timestamptz,
        created_at      timestamptz NOT NULL DEFAULT now(),
        resolved_at     timestamptz,
        actor           text
    )
    """,
    "CREATE INDEX approvals_run_idx ON approvals (run_id)",
    "CREATE INDEX approvals_state_idx ON approvals (state) WHERE state = 'PENDING'",
    # approval_batches: used by Phase 13 for grouped approvals
    """
    CREATE TABLE approval_batches (
        id          text PRIMARY KEY,
        run_id      text,
        state       text,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    # Forward-only state machine enforced at the DB level.
    """
    CREATE OR REPLACE FUNCTION approvals_check_transition()
    RETURNS TRIGGER AS $$
    DECLARE
        allowed_next TEXT[];
    BEGIN
        IF OLD.state = NEW.state THEN
            RETURN NEW;
        END IF;
        CASE OLD.state
            WHEN 'PENDING' THEN
                allowed_next := ARRAY['APPROVED','APPROVED_EDITED','REJECTED','EXPIRED','CANCELLED'];
            WHEN 'APPROVED' THEN
                allowed_next := ARRAY['CONSUMED','EXPIRED','INVALIDATED'];
            WHEN 'APPROVED_EDITED' THEN
                allowed_next := ARRAY['CONSUMED','EXPIRED','INVALIDATED'];
            WHEN 'CONSUMED' THEN
                allowed_next := ARRAY['EXECUTED','EXEC_FAILED','INVALIDATED'];
            ELSE
                -- REJECTED, EXPIRED, CANCELLED, EXECUTED, EXEC_FAILED, INVALIDATED are terminal
                allowed_next := ARRAY[]::TEXT[];
        END CASE;

        IF NOT (NEW.state = ANY(allowed_next)) THEN
            RAISE EXCEPTION 'invalid approval state transition: % -> %', OLD.state, NEW.state;
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER approvals_state_transition
    BEFORE UPDATE ON approvals
    FOR EACH ROW
    EXECUTE FUNCTION approvals_check_transition()
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only. Restore from backup instead.")
