"""chat_session_state and turn_logs for MindOS v2

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "20261120_0006"
down_revision = "20261116_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_session_state (
            session_id  UUID PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
            state       JSONB NOT NULL DEFAULT '{}',
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS turn_logs (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id           UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
            user_id              UUID REFERENCES users(id) ON DELETE SET NULL,
            tier                 INTEGER,
            mode                 TEXT,
            timings_ms           JSONB NOT NULL DEFAULT '{}',
            token_counts         JSONB NOT NULL DEFAULT '{}',
            evidence_eids        JSONB NOT NULL DEFAULT '[]',
            rerank_margin        DOUBLE PRECISION,
            verifier_ok          BOOLEAN,
            verifier_violations  JSONB NOT NULL DEFAULT '[]',
            spend_usd            DOUBLE PRECISION,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS turn_logs_session_created_idx "
        "ON turn_logs (session_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS turn_logs")
    op.execute("DROP TABLE IF EXISTS chat_session_state")
