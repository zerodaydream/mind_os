"""`ops doctor`: environment checks with a fix hint for each failure."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

import psycopg

from opsagent.domain import GEMINI_API_KEY, ModelRole
from opsagent.infra import ConfigError, Settings, SettingsCredentialProvider
from opsagent.persistence import current_revision, head_revision

Status = Literal["ok", "warn", "fail"]
REQUIRED_EXTENSIONS = ("vector", "pg_trgm")


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    fix: str | None = None


def run_doctor(settings: Settings) -> list[Check]:
    results: list[Check] = []
    db = _check_database(settings)
    results.append(db)
    if db.status == "ok":
        results.append(_check_extensions(settings))
        results.append(_check_schema(settings))
        results.append(_check_checkpointer(settings))
    results.append(_check_models(settings))
    results.append(_check_credentials(settings))
    results.append(_check_log_dir(settings))
    return results


def has_failure(results: list[Check]) -> bool:
    return any(c.status == "fail" for c in results)


def _check_database(settings: Settings) -> Check:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
            row = conn.execute("SHOW server_version").fetchone()
        return Check("database", "ok", f"PostgreSQL {row[0] if row else '?'} reachable")
    except psycopg.Error as exc:
        return Check(
            "database",
            "fail",
            f"cannot connect: {type(exc).__name__}",
            "Run `docker compose up -d db`, or set OPS_DATABASE_URL.",
        )


def _query(settings: Settings, sql: str) -> list[tuple[object, ...]]:
    with psycopg.connect(settings.database_url, connect_timeout=5) as conn:
        return conn.execute(sql).fetchall()


def _check_extensions(settings: Settings) -> Check:
    rows = _query(settings, "SELECT extname FROM pg_extension")
    present = {str(r[0]) for r in rows}
    missing = [e for e in REQUIRED_EXTENSIONS if e not in present]
    if missing:
        return Check(
            "extensions",
            "fail",
            f"missing: {', '.join(missing)}",
            "Run `ops db migrate`. The database image must include pgvector.",
        )
    return Check("extensions", "ok", ", ".join(REQUIRED_EXTENSIONS))


def _check_schema(settings: Settings) -> Check:
    try:
        current, head = current_revision(settings.database_url), head_revision()
    except Exception as exc:
        return Check("schema", "fail", f"cannot read revision: {type(exc).__name__}")
    if current != head:
        return Check(
            "schema", "fail", f"at {current or 'empty'}, expected {head}", "Run `ops db migrate`."
        )
    return Check("schema", "ok", f"revision {head}")


def _check_checkpointer(settings: Settings) -> Check:
    rows = _query(settings, "SELECT to_regclass('public.checkpoints') IS NOT NULL")
    if rows and rows[0][0]:
        return Check("checkpointer", "ok", "LangGraph tables present")
    return Check("checkpointer", "fail", "LangGraph tables missing", "Run `ops db migrate`.")


def _check_models(settings: Settings) -> Check:
    missing = []
    for role in (ModelRole.ORCHESTRATOR, ModelRole.SUBAGENT):
        try:
            settings.model_for(role)
        except ConfigError:
            missing.append(f"OPS_MODEL_{role.name}")
    if missing:
        return Check(
            "models", "warn", f"not set: {', '.join(missing)}", "Set them in .env before Phase 3."
        )
    return Check("models", "ok", "orchestrator and subagent models configured")


def _check_credentials(settings: Settings) -> Check:
    if SettingsCredentialProvider(settings).get(GEMINI_API_KEY):
        return Check("credentials", "ok", "Gemini API key present")
    return Check("credentials", "warn", "no Gemini API key", "Set GEMINI_API_KEY in .env.")


def _check_log_dir(settings: Settings) -> Check:
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return Check("logs", "fail", f"cannot create {settings.log_dir}: {exc.strerror}")
    if not os.access(settings.log_dir, os.W_OK):
        return Check("logs", "fail", f"{settings.log_dir} is not writable")
    return Check("logs", "ok", str(settings.log_dir))
