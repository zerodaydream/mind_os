import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic runs sync drivers; app runtime uses asyncpg.
def _sync_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        from backend.common.config_loader.settings import get_settings

        url = get_settings().database_url
    if not url:
        url = config.get_main_option("sqlalchemy.url")
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


from backend.common.database.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = _sync_url()
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = dict(config.get_section(config.config_ini_section, {}) or {})
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
