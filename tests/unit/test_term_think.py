from cli.term import _advance_think_reveal, _merge_think_partial


def test_advance_think_reveal_chunks() -> None:
    target = "Plan:\n○ step (pending)\n→ tool"
    got = ""
    while len(got) < len(target):
        nxt = _advance_think_reveal(target, got, chunk=4)
        assert len(nxt) > len(got) or nxt == target
        got = nxt
    assert got == target


def test_merge_think_appends_status_lines() -> None:
    prev = "→ Checking your calendar…"
    assert _merge_think_partial(prev, "→ Syncing Gmail…") == prev + "\n→ Syncing Gmail…"
    assert _merge_think_partial(prev, "→ Checking your calendar…") == prev


def test_merge_think_plan_replaces() -> None:
    plan = "Plan:\n○ step (pending)"
    assert _merge_think_partial("old", plan) == plan
    assert _merge_think_partial(plan, "→ tool") == plan + "\n→ tool"