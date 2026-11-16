from pathlib import Path

import pytest

from backend.services.integrations.mcp.convert_tools import (
    _try_ffmpeg,
    _unique_dest,
    build_convert_tools,
    convert_file_core,
)


@pytest.fixture
def fs_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDOS_HOME", str(tmp_path))
    monkeypatch.setenv("MINDOS_FS_ROOTS", str(tmp_path))
    return tmp_path


def test_unique_dest_skips_existing(fs_roots):
    taken = fs_roots / "shot.jpg"
    taken.write_bytes(b"old")
    assert _unique_dest(fs_roots / "shot.jpg").name == "shot (1).jpg"
    (fs_roots / "shot (1).jpg").write_bytes(b"older")
    assert _unique_dest(fs_roots / "shot.jpg").name == "shot (2).jpg"


def test_try_ffmpeg_wav_to_mp3(tmp_path, monkeypatch):
    src = tmp_path / "song.wav"
    dest = tmp_path / "song.mp3"
    src.write_bytes(b"not-a-real-wav")
    monkeypatch.setattr(
        "backend.services.integrations.mcp.convert_tools._which",
        lambda name: "ffmpeg" if name == "ffmpeg" else None,
    )

    def fake_run(cmd, **kwargs):
        assert "-vn" in cmd
        dest.write_bytes(b"fake-mp3")
        return 0, ""

    monkeypatch.setattr("backend.services.integrations.mcp.convert_tools._run", fake_run)
    assert _try_ffmpeg(src, dest, "mp3") is None
    assert dest.is_file()


def test_convert_core_rejects_same_format(fs_roots):
    src = fs_roots / "a.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n")
    dest = fs_roots / "a.png"
    msg = convert_file_core(src, dest, "png")
    assert "already" in msg.lower()


@pytest.mark.asyncio
async def test_batch_requires_confirmation(fs_roots, monkeypatch):
    monkeypatch.setenv("MINDOS_FS_ROOTS", str(fs_roots))
    a = fs_roots / "one.txt"
    b = fs_roots / "two.txt"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    tools = {t.__name__: t for t in build_convert_tools()}

    def fake_core(src: Path, dest: Path, to_fmt: str) -> str:
        dest.write_text("converted", encoding="utf-8")
        return f"Converted {src} -> {dest}"

    monkeypatch.setattr(
        "backend.services.integrations.mcp.convert_tools.convert_file_core",
        fake_core,
    )

    preview = await tools["mindos_convert_batch"](
        paths=[str(a), str(b)],
        to_format="md",
        confirmed=False,
    )
    assert "Would convert 2" in preview
    assert "confirmed=True" in preview

    done = await tools["mindos_convert_batch"](
        paths=[str(a), str(b)],
        to_format="md",
        confirmed=True,
    )
    assert "2/2 succeeded" in done
    assert (fs_roots / "one.md").is_file()


@pytest.mark.asyncio
async def test_single_file_uses_increment_when_output_exists(fs_roots, monkeypatch):
    monkeypatch.setenv("MINDOS_FS_ROOTS", str(fs_roots))
    src = fs_roots / "pic.heic"
    src.write_bytes(b"heic")
    (fs_roots / "pic.jpg").write_bytes(b"existing")
    tools = {t.__name__: t for t in build_convert_tools()}
    seen: list[Path] = []

    def fake_core(s: Path, dest: Path, to_fmt: str) -> str:
        seen.append(dest)
        dest.write_bytes(b"new")
        return f"Converted {s} -> {dest}"

    monkeypatch.setattr(
        "backend.services.integrations.mcp.convert_tools.convert_file_core",
        fake_core,
    )
    preview = await tools["mindos_convert_file"](path=str(src), to_format="jpg")
    assert "Output already exists" in preview
    assert "Shall I create" in preview
    assert "pic (1).jpg" in preview
    assert "confirmed=True" in preview

    msg = await tools["mindos_convert_file"](
        path=str(src), to_format="jpg", confirmed=True
    )
    assert "Converted" in msg
    assert seen and seen[0].name == "pic (1).jpg"
    assert src.read_bytes() == b"heic"