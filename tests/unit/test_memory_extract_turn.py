from backend.services.memories.core.extract_turn import _parse_drafts


def test_parse_memory_drafts() -> None:
    raw = '[{"kind":"preference","content":"I prefer morning meetings"}]'
    drafts = _parse_drafts(raw)
    assert len(drafts) == 1
    assert drafts[0]["approved"] is False
    assert "morning" in drafts[0]["content"]
