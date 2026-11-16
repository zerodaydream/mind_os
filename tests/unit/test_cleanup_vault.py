from cli.cleanup_cmd import clear_vault_dir


def test_clear_vault_dir_keeps_gitkeep(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    keep = vault / ".gitkeep"
    keep.write_text("", encoding="utf-8")
    nested = vault / "a" / "note.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert clear_vault_dir(str(vault)) == 1
    assert keep.is_file()
    assert not nested.exists()