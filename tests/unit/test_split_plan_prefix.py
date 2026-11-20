from cli.term import _split_plan_prefix


def test_list_only_message_not_duplicated_as_plan() -> None:
    text = "Open commitments:\n- item one\n- item two"
    plan, rest = _split_plan_prefix(text)
    assert plan == ""
    assert rest == text