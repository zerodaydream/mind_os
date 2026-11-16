from backend.services.integrations.core.gmail.sync import DEFAULT_GMAIL_FULL_INGEST_QUERY


def test_full_ingest_query_inbox_and_sent_not_attachment_only() -> None:
    q = DEFAULT_GMAIL_FULL_INGEST_QUERY
    assert "in:inbox" in q
    assert "in:sent" in q
    assert "has:attachment" not in q