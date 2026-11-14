from backend.services.memories.core.rules import extract_memory_rules


def test_preference_detected() -> None:
    drafts = extract_memory_rules("I prefer morning meetings.")
    assert any(d["kind"] == "preference" for d in drafts)
