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
async def test_locate_one_shot(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    (dl / "my_photo.png").write_bytes(b"2")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(
            user_task="how many my_photo files in downloads folder"
        )
    }
    found = await tools["mindos_fs_locate"]()
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
async def test_find_ignores_root_slash_when_user_named_folder(home_tree, monkeypatch):
    downloads = home_tree / "Downloads"
    target = downloads / "my_photo.jpg"
    target.write_bytes(b"j")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(
            user_task="find all my_photo files in downloads"
        )
    }
    found = await tools["mindos_fs_find"](pattern="my_photo", root="/")
    assert str(target) in found
    assert "entire disk" not in found.lower()


@pytest.mark.asyncio
async def test_find_scoped_to_resolved_folder(home_tree, monkeypatch):
    downloads = home_tree / "Downloads"
    target = downloads / "my_photo.jpg"
    target.write_bytes(b"j")
    (home_tree / "my_photo.jpg").write_bytes(b"wrong")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(
            user_task="convert my_photo.jpg in dowloads folder to png"
        )
    }
    found = await tools["mindos_fs_find"](pattern="my_photo.jpg")
    assert str(target) in found
    assert str(home_tree / "my_photo.jpg") not in found


@pytest.mark.asyncio
async def test_find_normalizes_trailing_star(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(user_task="my_photo files in downloads folder")
    }
    found = await tools["mindos_fs_find"](pattern="my_photo*")
    assert "my_photo.jpg" in found


@pytest.mark.asyncio
async def test_find_rejects_bare_star(home_tree):
    tools = {t.__name__: t for t in build_filesystem_tools()}
    msg = await tools["mindos_fs_find"](pattern="*")
    assert "too broad" in msg.lower()


@pytest.mark.asyncio
async def test_find_stem_in_nested_download_subfolder(home_tree):
    nested = home_tree / "Downloads" / "trips"
    nested.mkdir()
    deep = nested / "my_photo.jpg"
    deep.write_bytes(b"1")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(user_task="how many my_photo files in downloads")
    }
    found = await tools["mindos_fs_locate"]()
    assert str(deep) in found


@pytest.mark.asyncio
async def test_find_stem_prefix_in_downloads(home_tree):
    dl = home_tree / "Downloads"
    (dl / "my_photo.jpg").write_bytes(b"1")
    (dl / "my_photo.png").write_bytes(b"2")
    (dl / "other.jpg").write_bytes(b"x")
    tools = {
        t.__name__: t
        for t in build_filesystem_tools(
            user_task="how many my_photo files in my downloads folder"
        )
    }
    found = await tools["mindos_fs_find"](pattern="my_photo")
    assert "my_photo.jpg" in found
    assert "my_photo.png" in found
    assert "other.jpg" not in found


def test_resolve_path_from_task(home_tree):
    p = resolve_path_from_user_task(
        "my_photo.jpg",
        "convert my_photo in dowloads folder",
    )
    assert p.endswith("Downloads/my_photo.jpg")