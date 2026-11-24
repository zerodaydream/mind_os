"""Baseline schema.

Creates conversations, messages, agent runs, the append-only audit log, integration states,
and provider usage. LangGraph checkpoint tables are created by `PostgresSaver.setup()`, not here.

The audit table is protected by triggers that reject UPDATE, DELETE, and TRUNCATE for every role,
including the owner. In production, run the app as a role that does not own the table so it
also cannot drop the triggers.

Revision ID: 0001
Revises:
Create Date: 2026-11-22
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE conversations (
    id          text PRIMARY KEY,
    title       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE messages (
    id               text PRIMARY KEY,
    conversation_id  text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role             text NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content          jsonb NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at);

CREATE TABLE agent_runs (
    id               text PRIMARY KEY,
    conversation_id  text NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    thread_id        text NOT NULL,
    state            text NOT NULL CHECK (state IN (
                         'RUNNING', 'AWAITING_APPROVAL', 'AWAITING_AUTH',
                         'FINISHED', 'FAILED', 'CANCELLED')),
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    error            text
);
CREATE INDEX agent_runs_open_idx ON agent_runs (state)
    WHERE state IN ('RUNNING', 'AWAITING_APPROVAL', 'AWAITING_AUTH');
CREATE INDEX agent_runs_conversation_idx ON agent_runs (conversation_id, started_at);

CREATE TABLE audit_events (
    id            bigserial PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    event_type    text NOT NULL,
    actor         text NOT NULL,
    subject_type  text,
    subject_id    text,
    run_id        text,
    data          jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_events_run_idx ON audit_events (run_id);
CREATE INDEX audit_events_type_ts_idx ON audit_events (event_type, ts);

CREATE FUNCTION audit_events_reject_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only: % is not allowed', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_reject_change();

CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_reject_change();

CREATE TABLE integration_states (
    provider    text NOT NULL,
    account_id  text NOT NULL,
    status      text NOT NULL CHECK (status IN (
                    'CONNECTED', 'NEEDS_REAUTH', 'NEEDS_SCOPE', 'REVOKED')),
    scopes      text[] NOT NULL DEFAULT '{}',
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider, account_id)
);

CREATE TABLE provider_usage (
    id                bigserial PRIMARY KEY,
    ts                timestamptz NOT NULL DEFAULT now(),
    run_id            text,
    role              text NOT NULL,
    model             text NOT NULL,
    input_tokens      integer NOT NULL DEFAULT 0,
    output_tokens     integer NOT NULL DEFAULT 0,
    cached_tokens     integer NOT NULL DEFAULT 0,
    reasoning_tokens  integer NOT NULL DEFAULT 0,
    latency_ms        integer,
    cost_usd          numeric(12, 6)
);
CREATE INDEX provider_usage_run_idx ON provider_usage (run_id);
CREATE INDEX provider_usage_ts_idx ON provider_usage (ts);
"""


def upgrade() -> None:
    for statement in _split(_SQL):
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only. Restore from backup instead.")


def _split(script: str) -> list[str]:
    """Split on semicolons that end a statement, keeping $$ function bodies whole."""
    statements: list[str] = []
    buf: list[str] = []
    in_body = False
    for line in script.splitlines():
        buf.append(line)
        if line.count("$$") % 2 == 1:
            in_body = not in_body
        if not in_body and line.rstrip().endswith(";"):
            text = "\n".join(buf).strip()
            if text:
                statements.append(text)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements
