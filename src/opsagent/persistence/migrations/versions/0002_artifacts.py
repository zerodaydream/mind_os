"""Artifacts: large tool results stored out of the model context (ARCHITECTURE section 4.2).

Forward-only. `provider_usage` already exists from the baseline and gains no columns here.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_STATEMENTS = (
    """
    CREATE TABLE artifacts (
        id          text PRIMARY KEY,
        run_id      text,
        media_type  text NOT NULL DEFAULT 'text/plain',
        content     text NOT NULL,
        size        integer NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX artifacts_run_idx ON artifacts (run_id)",
    "CREATE INDEX artifacts_created_idx ON artifacts (created_at)",
)


def upgrade() -> None:
    for statement in _STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise NotImplementedError("Migrations are forward-only. Restore from backup instead.")
