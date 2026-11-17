from backend.services.integrations.core.gmail.sync import _gmail_query_label


def test_gmail_query_label_truncates() -> None:
    long = "a" * 100
    assert _gmail_query_label(long).endswith("…")
    assert len(_gmail_query_label(long)) == 72