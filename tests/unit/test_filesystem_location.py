from pathlib import Path

import pytest

from backend.services.integrations.mcp.filesystem_tools import (
    _guess_find_pattern,
    _match_home_dir,
    build_filesystem_tools,
    resolve_folder_from_user_text,
    resolve_path_from_user_task,
)


@pytest.fixture
def home_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MINDOS_FS_ROOTS", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Workstuff").mkdir()
    return tmp_path


def test_guess_find_pattern_from_message():
    assert _guess_find_pattern("find all my_photo files in downloads") == "my_photo"
    assert _guess_find_pattern("how many my_photo files in downloads") == "my_photo"
    assert _guess_find_pattern("locate budget.xlsx in workstuff") == "budget.xlsx"


@pytest.mark.asyncio
async def test_locate_under_root(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    (dl / "my_photo.png").write_bytes(b"2")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_locate"](name="my_photo", root=str(dl))
    assert "my_photo.jpg" in found
    assert "my_photo.png" in found


def test_match_home_dir_typo(home_tree):
    hit = _match_home_dir("dowloads")
    assert hit == (home_tree / "Downloads").resolve()


def test_resolve_folder_from_natural_language(home_tree):
    got = resolve_folder_from_user_text("convert my_photo.jpg in dowloads folder to png")
    assert got == (home_tree / "Downloads").resolve()


def test_resolve_custom_folder_name(home_tree):
    got = resolve_folder_from_user_text("find budget.xlsx in workstuff folder")
    assert got == (home_tree / "Workstuff").resolve()


@pytest.mark.asyncio
async def test_find_rejects_root_slash(home_tree):
    downloads = home_tree / "Downloads"
    target = downloads / "my_photo.jpg"
    target.write_bytes(b"j")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_find"](pattern="my_photo", root="/")
    assert "not /" in found.lower() or "folder" in found.lower()


@pytest.mark.asyncio
async def test_find_scoped_to_root(home_tree):
    downloads = home_tree / "Downloads"
    target = downloads / "my_photo.jpg"
    target.write_bytes(b"j")
    (home_tree / "my_photo.jpg").write_bytes(b"wrong")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_find"](pattern="my_photo.jpg", root=str(downloads))
    assert str(target) in found
    assert str(home_tree / "my_photo.jpg") not in found


@pytest.mark.asyncio
async def test_find_normalizes_trailing_star(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_find"](pattern="my_photo*", root=str(dl))
    assert "my_photo.jpg" in found


@pytest.mark.asyncio
async def test_find_rejects_bare_star(home_tree):
    tools = {t.__name__: t for t in build_filesystem_tools()}
    msg = await tools["mindos_fs_find"](pattern="*", root=str(home_tree))
    assert "too broad" in msg.lower()


@pytest.mark.asyncio
async def test_find_stem_in_nested_download_subfolder(home_tree):
    nested = home_tree / "Downloads" / "trips"
    nested.mkdir()
    deep = nested / "my_photo.jpg"
    deep.write_bytes(b"1")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_locate"](name="my_photo", root=str(home_tree / "Downloads"))
    assert str(deep) in found


@pytest.mark.asyncio
async def test_find_stem_prefix_in_downloads(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    (dl / "my_photo.png").write_bytes(b"2")
    (dl / "other.jpg").write_bytes(b"x")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_find"](pattern="my_photo", root=str(dl))
    assert "my_photo.jpg" in found
    assert "my_photo.png" in found
    assert "other.jpg" not in found


@pytest.mark.asyncio
async def test_glob_and_grep(home_tree):
    src = home_tree / "Workstuff" / "needle.txt"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("alpha UNIQUE_TOKEN beta\n", encoding="utf-8")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    paths = await tools["mindos_fs_glob"](glob_pattern="**/*.txt", target_directory=str(home_tree))
    assert str(src) in paths
    hits = await tools["mindos_fs_grep"](
        pattern="UNIQUE_TOKEN",
        path=str(home_tree),
        glob="*.txt",
    )
    assert "UNIQUE_TOKEN" in hits


@pytest.mark.asyncio
async def test_glob_stops_at_max_results(home_tree):
    dl = home_tree / "Downloads"
    dl.mkdir(parents=True, exist_ok=True)
    for i in range(30):
        (dl / f"file_{i}.txt").write_text("x", encoding="utf-8")
    tools = {t.__name__: t for t in build_filesystem_tools()}
    found = await tools["mindos_fs_glob"](
        glob_pattern="*.txt",
        target_directory=str(dl),
        max_results=3,
    )
    assert found.count("\n") <= 4  # 3 paths + optional cap line


def test_resolve_path_from_task(home_tree):
    p = resolve_path_from_user_task(
        "my_photo.jpg",
        "convert my_photo in dowloads folder",
    )
    assert p.endswith("Downloads/my_photo.jpg")