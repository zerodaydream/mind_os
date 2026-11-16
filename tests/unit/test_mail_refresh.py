from backend.services.agents.core.turn.turn_context import preflight_prompt_block


def test_preflight_prompt_block_nonempty() -> None:
    block = preflight_prompt_block(["I'll sync Gmail.", "Gmail sync finished (ingested 2)."])
    assert "Steps already taken" in block
    assert "sync Gmail" in block