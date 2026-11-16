from backend.services.integrations.core.gmail.sync import (
    _gmail_batch_start,
    _gmail_fast_index_stale,
    _gmail_should_verify_body,
)
from backend.services.ingest.core.gmail.policy import INGEST_POLICY_VERSION


def test_batch_start_after_message() -> None:
    batch = ["a", "b", "c"]
    assert _gmail_batch_start(batch, None) == 0
    assert _gmail_batch_start(batch, "b") == 2
    assert _gmail_batch_start(batch, "z") == 0


def test_fast_index_stale_policy_and_chunker() -> None:
    art = type(
        "A",
        (),
        {
            "metadata_": {"ingest_policy_version": "old"},
            "content_hash": "x",
        },
    )()
    assert _gmail_fast_index_stale(art, type("S", (), {"chunker_version": "v2"})(), []) is True
    art.metadata_ = {"ingest_policy_version": INGEST_POLICY_VERSION}
    assert _gmail_fast_index_stale(art, type("S", (), {"chunker_version": "v2"})(), ["v1"]) is True
    assert (
        _gmail_fast_index_stale(
            art, type("S", (), {"chunker_version": "v2"})(), ["v2"]
        )
        is False
    )


def test_should_verify_body_when_attachments() -> None:
    art = type("A", (), {"metadata_": {"attachments": [{"name": "x.pdf"}]}})()
    assert _gmail_should_verify_body(art) is True
    art.metadata_ = {}
    assert _gmail_should_verify_body(art) is False