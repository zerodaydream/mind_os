from backend.services.agents.core.routing.turn_prep import fallback_turn_prep, fast_path_turn_prep


def test_fast_path_slash_command_is_execute() -> None:
    prep = fast_path_turn_prep("/do sync gmail")
    assert prep is not None
    assert prep.intent == "execute"


def test_fallback_workspace_plan() -> None:
    for msg in (
        "who owes me a reply?",
        "show my inbox",
        "delete the first 2 upcoming meetings in my calender",
    ):
        prep = fallback_turn_prep(msg, None)
        assert prep.in_scope is True
        assert prep.intent in ("plan", "execute")