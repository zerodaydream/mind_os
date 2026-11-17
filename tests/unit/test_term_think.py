from cli.term import _advance_think_reveal, _merge_think_partial


def test_advance_think_reveal_chunks() -> None:
    target = "Planning moves:\n○ step (pending)\n● step (in progress)"
    got = ""
    while len(got) < len(target):
        nxt = _advance_think_reveal(target, got, chunk=4)
        assert len(nxt) > len(got) or nxt == target
        got = nxt
    assert got == target


def test_merge_think_appends_status_lines() -> None:
    prev = "[tool] Checking your calendar…"
    assert _merge_think_partial(prev, "[tool] Syncing Gmail…") == prev + "\n[tool] Syncing Gmail…"
    assert _merge_think_partial(prev, "[tool] Checking your calendar…") == prev


def test_merge_think_plan_replaces() -> None:
    plan = "Planning moves:\n○ step (pending)"
    assert _merge_think_partial("old", plan) == plan
    assert _merge_think_partial(plan, "[tool] tool") == plan + "\n[tool] tool"