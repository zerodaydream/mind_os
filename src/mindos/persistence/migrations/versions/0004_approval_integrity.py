"""Approval integrity: resumable context, immutable payloads, expiry at the database.

Forward-only. Fixes found in the Phase 05 review:

* An approval row now records where its run is paused (`thread_id`, `conversation_id`,
  `tool_call_id`) so startup reconciliation can re-deliver a decision after a restart, and
  `delivered_at` records that the paused graph has received it.
* `original_payload_hash` keeps the hash the user first saw when they edit (ARCHITECTURE 5.4).
* The transition trigger now also makes the approved payload and identity columns immutable
  (a payload may change only on PENDING -> APPROVED_EDITED) and refuses to approve or consume
  an expired row, so these guarantees hold even against raw SQL.
* `tool_executions` gains `UNKNOWN`: the outcome of a side effect that was in flight when the
  process died is not known, and recording it as FAILED would be a lie.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_STATEMENTS = (
    """
    ALTER TABLE approvals
        ADD COLUMN thread_id             text,
        ADD COLUMN conversation_id       text,
        ADD COLUMN tool_call_id          text,
        ADD COLUMN original_payload_hash text,
        ADD COLUMN delivered_at          timestamptz
    """,
    # Rows whose decision has not reached the paused graph yet: the reconciliation worklist.
    """
    CREATE INDEX approvals_undelivered_idx ON approvals (state)
        WHERE delivered_at IS NULL AND thread_id IS NOT NULL
    """,
    "ALTER TABLE tool_executions DROP CONSTRAINT tool_executions_state_check",
    """
    ALTER TABLE tool_executions ADD CONSTRAINT tool_executions_state_check
        CHECK (state IN ('STARTED','FINISHED','FAILED','AWAITING_AUTH','UNKNOWN'))
    """,
    """
    CREATE OR REPLACE FUNCTION approvals_check_transition()
    RETURNS TRIGGER AS $$
    DECLARE
        allowed_next TEXT[];
    BEGIN
        -- Identity never changes.
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.run_id IS DISTINCT FROM OLD.run_id
           OR NEW.tool IS DISTINCT FROM OLD.tool
           OR NEW.tool_version IS DISTINCT FROM OLD.tool_version
           OR NEW.principal IS DISTINCT FROM OLD.principal
           OR NEW.risk IS DISTINCT FROM OLD.risk
           OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.thread_id IS DISTINCT FROM OLD.thread_id
           OR NEW.conversation_id IS DISTINCT FROM OLD.conversation_id
           OR NEW.tool_call_id IS DISTINCT FROM OLD.tool_call_id THEN
            RAISE EXCEPTION 'approval identity columns are immutable';
        END IF;

        -- The approved payload is fixed once a decision exists. The only legal change is the
        -- user's edit, which re-hashes it on the way PENDING -> APPROVED_EDITED.
        IF (NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
            OR NEW.args_json IS DISTINCT FROM OLD.args_json)
           AND NOT (OLD.state = 'PENDING' AND NEW.state = 'APPROVED_EDITED') THEN
            RAISE EXCEPTION 'approval payload is immutable after a decision';
        END IF;

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

        -- An expired approval can be neither granted nor consumed (ARCHITECTURE 5.4).
        IF NEW.state IN ('APPROVED','APPROVED_EDITED','CONSUMED')
           AND OLD.expires_at IS NOT NULL AND OLD.expires_at <= now() THEN
            RAISE EXCEPTION 'approval expired';
        END IF;

        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only. Restore from backup instead.")
