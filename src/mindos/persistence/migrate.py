"""Schema management: Alembic for our tables, LangGraph `setup()` for checkpoint tables."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from mindos.persistence.db import sqlalchemy_url

_MIGRATIONS = Path(__file__).parent / "migrations"


def _config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    # ConfigParser treats '%' as interpolation; escape it.
    cfg.set_main_option("sqlalchemy.url", sqlalchemy_url(url).replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    return ScriptDirectory.from_config(_config("postgresql://x/x")).get_current_head()


def current_revision(url: str) -> str | None:
    engine = create_engine(sqlalchemy_url(url), poolclass=NullPool)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def upgrade(url: str) -> None:
    """Apply our migrations, then let LangGraph create or update its checkpoint tables."""
    command.upgrade(_config(url), "head")
    setup_checkpointer(url)


def setup_checkpointer(url: str) -> None:
    with PostgresSaver.from_conn_string(url) as saver:
        saver.setup()


def schema_is_current(url: str) -> bool:
    return current_revision(url) == head_revision()


# Checkpoints are read back through a restricted deserializer (ARCHITECTURE section 9.1): a
# tampered database cannot make us import arbitrary types. Add a type here when state needs it.
_CHECKPOINT_TYPES = (
    ("langchain_core", "messages", "ai", "AIMessage"),
    ("langchain_core", "messages", "human", "HumanMessage"),
    ("langchain_core", "messages", "tool", "ToolMessage"),
    ("langchain_core", "messages", "system", "SystemMessage"),
)


def checkpoint_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=list(_CHECKPOINT_TYPES))


@asynccontextmanager
async def open_checkpointer(url: str) -> AsyncIterator[AsyncPostgresSaver]:
    """An async LangGraph checkpointer on its own connection (autocommit, dict rows)."""
    async with AsyncPostgresSaver.from_conn_string(url, serde=checkpoint_serializer()) as saver:
        yield saver
