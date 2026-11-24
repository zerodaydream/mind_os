"""Deferred approvals: a subagent's approval request that does not pause the run.

Forward-only. A subagent has no checkpointer, so it cannot wait for a human. Its approval row now
records the agent that asked (`agent`) and that nobody is paused on it (`deferred`): the
application runs the approved call itself as that agent (ADR 0005). Both columns join the
identity set the trigger keeps immutable, so the agent that may run the call cannot be changed
after the user has seen it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-25
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_STATEMENTS = (
    "ALTER TABLE approvals ADD COLUMN agent text, ADD COLUMN deferred boolean NOT NULL DEFAULT false",
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
           OR NEW.tool_call_id IS DISTINCT FROM OLD.tool_call_id
           OR NEW.agent IS DISTINCT FROM OLD.agent
           OR NEW.deferred IS DISTINCT FROM OLD.deferred THEN
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
