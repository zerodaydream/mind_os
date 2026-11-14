"""initial schema

Revision ID: 0001
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema-core.sql"


def upgrade() -> None:
    ctx = op.get_context()
    with ctx.autocommit_block():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    sql = _SCHEMA.read_text(encoding="utf-8")
    statements = []
    buf: list[str] = []
    for line in sql.splitlines():
        if line.strip().upper().startswith("CREATE EXTENSION"):
            continue
        buf.append(line)
        if ";" in line.split("--", 1)[0]:
            statements.append("\n".join(buf))
            buf = []
    for stmt in statements:
        cleaned = stmt.strip()
        if cleaned:
            op.execute(cleaned)


def downgrade() -> None:
    tables = [
        "audit_log",
        "llm_cache",
        "llm_usage_events",
        "outbox_events",
        "chat_messages",
        "chat_sessions",
        "memories",
        "tensions",
        "commitments",
        "edges",
        "entities",
        "chunk_embeddings",
        "artifact_chunks",
        "artifacts",
        "integration_accounts",
        "users",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
