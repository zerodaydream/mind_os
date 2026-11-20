"""Extend memories for v2 semantic memory plane

Revision ID: 20261120_0007
Revises: 20261120_0006
"""

from alembic import op

revision = "20261120_0007"
down_revision = "20261120_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE memories
          ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active',
          ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'user',
          ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
          ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS valid_to TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS supersedes_id UUID REFERENCES memories(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS use_count INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS embedding vector(768)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            session_id      UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
            summary         TEXT NOT NULL,
            embedding       vector(768),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS session_summaries_user_created_idx "
        "ON session_summaries (user_id, created_at DESC)"
    )
    # Backfill: unapproved rows become proposed.
    op.execute(
        "UPDATE memories SET status = 'proposed' WHERE approved = false AND status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_summaries")
    op.execute(
        """
        ALTER TABLE memories
          DROP COLUMN IF EXISTS embedding,
          DROP COLUMN IF EXISTS last_used_at,
          DROP COLUMN IF EXISTS use_count,
          DROP COLUMN IF EXISTS supersedes_id,
          DROP COLUMN IF EXISTS valid_to,
          DROP COLUMN IF EXISTS valid_from,
          DROP COLUMN IF EXISTS confidence,
          DROP COLUMN IF EXISTS origin,
          DROP COLUMN IF EXISTS status
        """
    )
