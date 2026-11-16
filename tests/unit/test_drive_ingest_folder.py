from backend.services.integrations.core.gdrive.drive_store import (
    _DEFAULT_INGEST_FOLDER,
    _attachment_type_folder,
    _ingest_folder_display_name,
)


def test_ingest_folder_default_name(monkeypatch) -> None:
    monkeypatch.delenv("GDRIVE_INGEST_FOLDER", raising=False)
    monkeypatch.delenv("GDRIVE_MCP_INGEST_FOLDER", raising=False)
    assert _ingest_folder_display_name() == _DEFAULT_INGEST_FOLDER


def test_ingest_folder_env_override(monkeypatch) -> None:
    monkeypatch.setenv("GDRIVE_INGEST_FOLDER", "MindOS_Ingestion_Pipeline_Artifacts")
    assert _ingest_folder_display_name() == "MindOS_Ingestion_Pipeline_Artifacts"


def test_ingest_folder_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("GDRIVE_INGEST_FOLDER", raising=False)
    monkeypatch.setenv("GDRIVE_MCP_INGEST_FOLDER", "legacy-folder")
    assert _ingest_folder_display_name() == "legacy-folder"