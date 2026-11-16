from backend.services.integrations.core.gmail.sync import DEFAULT_GMAIL_FULL_INGEST_QUERY


def test_full_ingest_query_excludes_promotions_and_unsubscribe() -> None:
    q = DEFAULT_GMAIL_FULL_INGEST_QUERY
    assert "promotions" in q
    assert "unsubscribe" in q
    assert "spam" in q
    assert "has:attachment" in q
    assert "from:me" not in q