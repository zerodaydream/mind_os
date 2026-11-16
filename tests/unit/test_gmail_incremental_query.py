from datetime import datetime, timezone

from backend.services.integrations.core.gmail.sync import (
    DEFAULT_GMAIL_FULL_INGEST_QUERY,
    _gmail_after_clause,
    _gmail_list_query,
)
from backend.common.config_loader.settings import Settings


def test_sync_default_uses_full_ingest_query() -> None:
    q = _gmail_list_query(Settings())
    assert q == DEFAULT_GMAIL_FULL_INGEST_QUERY


def test_gmail_after_clause_from_occurred_at() -> None:
    assert _gmail_after_clause(None) == ""
    ts = datetime(2026, 11, 16, 13, 30, tzinfo=timezone.utc)
    assert _gmail_after_clause(ts) == "after:2026/11/16"