from backend.services.memories.core.rules import extract_memory_rules


def test_preference_detected() -> None:
    drafts = extract_memory_rules("I prefer morning meetings.")
    assert any(d["kind"] == "preference" for d in drafts)
    pref = next(d for d in drafts if d["kind"] == "preference")
    assert pref["approved"] is False
    assert pref["content"] == "I prefer morning meetings."


def test_does_not_store_whole_artifact_prefix() -> None:
    body = (
        "Thanks for the invoice.\n\n"
        "I prefer morning meetings when possible.\n\n"
        + ("filler text about shipments. " * 80)
    )
    drafts = extract_memory_rules(body)
    assert drafts
    assert all(len(d["content"]) < 200 for d in drafts)
    assert "filler text" not in drafts[0]["content"]
