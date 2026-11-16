import pytest

from backend.services.integrations.mcp import personal_tools as pt


@pytest.mark.asyncio
async def test_attachment_lines_skips_missing_drive_file(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _gone(_fid: str) -> bool:
        return False

    monkeypatch.setattr(pt, "drive_file_exists", _gone)
    meta = {
        "attachments": [
            {"filename": "a.pdf", "drive_file_id": "dead", "drive_url": "https://drive.google.com/file/d/dead/view"},
        ],
    }
    lines = await pt._gmail_attachment_lines(meta)
    assert lines[0] == "  attachment · a.pdf (drive missing; re-ingest)"


@pytest.mark.asyncio
async def test_attachment_lines_keeps_live_file(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ok(_fid: str) -> bool:
        return True

    monkeypatch.setattr(pt, "drive_file_exists", _ok)
    meta = {
        "attachments": [
            {"filename": "a.pdf", "drive_file_id": "ABC", "drive_url": "https://drive.google.com/file/d/ABC/view"},
        ],
    }
    lines = await pt._gmail_attachment_lines(meta)
    assert "a.pdf" in lines[0]
    assert "ABC" in lines[0]