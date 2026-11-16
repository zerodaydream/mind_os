from pathlib import Path


def test_gmail_sync_does_not_double_enqueue_artifact_process() -> None:
    src = Path("backend/services/integrations/core/gmail/sync.py").read_text()
    assert 'enqueue(session, "artifact.process"' not in src


def test_manual_ingest_uses_enqueue_artifact_process() -> None:
    src = Path("backend/services/ingest/core/manual.py").read_text()
    assert "enqueue_artifact_process" in src