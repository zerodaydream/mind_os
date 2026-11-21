from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

# Never inherit dev tracing or keys from the shell into tests.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

ADMIN_URL = os.environ.get(
    "OPS_TEST_ADMIN_URL", "postgresql://opsagent:opsagent@localhost:5433/postgres"
)


def _url_for(dbname: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + dbname


@pytest.fixture
def db_url() -> Iterator[str]:
    """A fresh empty database on the local compose Postgres, dropped after the test."""
    name = f"opsagent_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = psycopg.connect(ADMIN_URL, autocommit=True, connect_timeout=3)
    except psycopg.Error:
        pytest.skip("PostgreSQL not reachable. Run `docker compose up -d db`.")
    with admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            yield _url_for(name)
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )
