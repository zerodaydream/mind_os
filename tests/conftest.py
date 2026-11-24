from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import keyring
import psycopg
import pytest
from keyring.backends.null import Keyring as NullKeyring
from psycopg import sql

# Never inherit dev tracing or keys from the shell into tests.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

# Tests never touch the real Keychain: the null backend stores nothing and finds nothing.
keyring.set_keyring(NullKeyring())

ADMIN_URL = os.environ.get(
    "MINDOS_TEST_ADMIN_URL", "postgresql://mindos:mindos@localhost:5433/postgres"
)


def _url_for(dbname: str) -> str:
    return ADMIN_URL.rsplit("/", 1)[0] + "/" + dbname


@pytest.fixture
def db_url() -> Iterator[str]:
    """A fresh empty database on the local compose Postgres, dropped after the test."""
    name = f"mindos_test_{uuid.uuid4().hex[:10]}"
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
