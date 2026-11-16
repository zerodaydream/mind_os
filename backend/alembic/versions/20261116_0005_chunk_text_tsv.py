"""GIN index for chunk full-text search

Revision ID: 20261116_0005
Revises: 20261116_0004
Create Date: 2026-11-16
"""

from alembic import op

revision = "20261116_0005"
down_revision = "20261116_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE artifact_chunks
        ADD COLUMN IF NOT EXISTS text_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS artifact_chunks_text_tsv_idx "
        "ON artifact_chunks USING GIN (text_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS artifact_chunks_text_tsv_idx")
    op.execute("ALTER TABLE artifact_chunks DROP COLUMN IF EXISTS text_tsv")