from backend.services.agents.core.turn.turn_retrieval import format_gmail_sync_thinking


def test_sync_thinking_table_includes_counts() -> None:
    md = format_gmail_sync_thinking(
        {"ingested": 3, "skipped": 10, "scanned": 50}, processed=3
    )
    assert "Gmail sync" in md
    assert "newly ingested" in md
    assert "| 3 |" in md


def test_sync_thinking_no_outbox_when_nothing_ingested() -> None:
    md = format_gmail_sync_thinking({"ingested": 0, "skipped": 1, "scanned": 2}, processed=None)
    assert "outbox indexed | — |" in md