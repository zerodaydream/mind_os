from __future__ import annotations

import psycopg
import pytest
from typer.testing import CliRunner

from mindos.bootstrap import has_failure, run_doctor
from mindos.cli import app
from mindos.domain import bind
from mindos.infra import Settings
from mindos.persistence import (
    AuditRepository,
    current_revision,
    head_revision,
    open_pool,
    schema_is_current,
    upgrade,
)

pytestmark = pytest.mark.integration

TABLES = {
    "conversations",
    "messages",
    "agent_runs",
    "audit_events",
    "integration_states",
    "provider_usage",
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "artifacts",
    "tool_executions",
    "approvals",
    "approval_batches",
}


def _scalar(url: str, sql: str) -> object:
    with psycopg.connect(url) as conn:
        row = conn.execute(sql).fetchone()
    return row[0] if row else None


def test_migrate_creates_schema_and_is_idempotent(db_url: str) -> None:
    assert current_revision(db_url) is None
    upgrade(db_url)
    upgrade(db_url)
    assert current_revision(db_url) == head_revision()
    assert schema_is_current(db_url)
    with psycopg.connect(db_url) as conn:
        names = {
            r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        }
        exts = {r[0] for r in conn.execute("SELECT extname FROM pg_extension")}
    assert names >= TABLES
    assert {"vector", "pg_trgm"} <= exts


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET actor = 'x'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
def test_audit_table_is_append_only(db_url: str, statement: str) -> None:
    upgrade(db_url)
    with psycopg.connect(db_url, autocommit=True) as conn:
        conn.execute("INSERT INTO audit_events (event_type, actor) VALUES ('t', 'USER')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="append-only"):
            conn.execute(statement)
        row = conn.execute("SELECT count(*) FROM audit_events").fetchone()
    assert row is not None and row[0] == 1


async def test_audit_repository_redacts_and_tags_run(db_url: str) -> None:
    upgrade(db_url)
    pool = await open_pool(db_url, max_size=2)
    try:
        repo = AuditRepository(pool)
        with bind(run_id="run_1"):
            audit_id = await repo.append(
                event_type="tool.executed",
                actor="AGENT",
                data={"api_key": "plain", "note": "hf_" + "a" * 30, "ok": 1},
            )
    finally:
        await pool.close()
    with psycopg.connect(db_url) as conn:
        row = conn.execute(
            "SELECT run_id, data FROM audit_events WHERE id = %s", (audit_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "run_1"
    assert row[1] == {"api_key": "[REDACTED]", "note": "[REDACTED]", "ok": 1}


def test_doctor_reports_healthy_database(db_url: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    upgrade(db_url)
    settings = Settings(_env_file=None, database_url=db_url, log_dir=tmp_path)
    results = {c.name: c for c in run_doctor(settings)}
    assert not has_failure(list(results.values()))
    assert results["database"].status == "ok"
    assert results["extensions"].status == "ok"
    assert results["schema"].status == "ok"
    assert results["checkpointer"].status == "ok"


def test_doctor_flags_unmigrated_database(db_url: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, database_url=db_url, log_dir=tmp_path)
    results = {c.name: c for c in run_doctor(settings)}
    assert results["schema"].status == "fail"
    assert "mindos db migrate" in (results["schema"].fix or "")


def test_cli_doctor_and_migrate(db_url: str, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.setenv("MINDOS_DATABASE_URL", db_url)
    monkeypatch.setenv("MINDOS_LOG_DIR", str(tmp_path / "logs"))
    runner = CliRunner()
    assert runner.invoke(app, ["doctor"]).exit_code == 1
    migrated = runner.invoke(app, ["db", "migrate"])
    assert migrated.exit_code == 0, migrated.output
    assert runner.invoke(app, ["db", "status"]).exit_code == 0
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    assert "mindos 0.1.0" in runner.invoke(app, ["--version"]).output
