from cli.term import _split_plan_prefix


def test_split_leading_todos() -> None:
    raw = "1. Sync Gmail\n2. Search again\n\nHere is the answer."
    plan, rest = _split_plan_prefix(raw)
    assert "Sync Gmail" in plan
    assert rest.startswith("Here is the answer.")


def test_no_split_when_opens_with_paragraph() -> None:
    raw = "Here is the answer.\n\n- footnote"
    plan, rest = _split_plan_prefix(raw)
    assert plan == ""
    assert rest == raw