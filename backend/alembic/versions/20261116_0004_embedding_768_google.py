"""pgvector 768-dim for gemini-embedding-001

Revision ID: 20261116_0004
Revises: 20261116_0003
Create Date: 2026-11-16
"""

from alembic import op

revision = "20261116_0004"
down_revision = "20261116_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM chunk_embeddings")
    op.execute("DROP INDEX IF EXISTS chunk_embeddings_hnsw")
    op.execute("ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE vector(768)")
    op.execute(
        "CREATE INDEX chunk_embeddings_hnsw ON chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DELETE FROM chunk_embeddings")
    op.execute("DROP INDEX IF EXISTS chunk_embeddings_hnsw")
    op.execute("ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE vector(1024)")
    op.execute(
        "CREATE INDEX chunk_embeddings_hnsw ON chunk_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )