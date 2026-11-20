from backend.services.agents.core.contracts.evidence import EvidencePack
from backend.services.agents.core.contracts.turn_plan import TurnPlan
from backend.services.agents.core.routing.turn_prep import TurnPrep
from backend.services.agents.core.turn.orchestrate import resolve_deep_tier
from backend.services.agents.core.turn.turn_retrieval import TurnRetrieval


def _prep(intent: str = "plan", **kw) -> TurnPrep:
    base = dict(
        in_scope=True,
        intent=intent,
        task="x",
        search_query="x",
        prefetch_mail=False,
        prefetch_commitments=False,
        prefetch_memories=False,
    )
    base.update(kw)
    return TurnPrep(**base)  # type: ignore[arg-type]


def _plan(**kw) -> TurnPlan:
    defaults = {
        "scope": "in",
        "intent": "plan",
        "writes_external": False,
        "task": "x",
    }
    defaults.update(kw)
    return TurnPlan(**defaults)


def test_execute_intent_tier_2() -> None:
    plan = _plan(intent="execute", writes_external=True)
    use_deep, tier = resolve_deep_tier(
        plan, "list calendar", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is True
    assert tier == 2


def test_slash_command_needs_deep() -> None:
    plan = _plan(intent="plan")
    use_deep, _ = resolve_deep_tier(
        plan, "/do sync gmail", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is True


def test_plan_list_mail_uses_synth() -> None:
    ctx = TurnRetrieval(message="x", has_context=True)
    plan = _plan(intent="plan", writes_external=False)
    use_deep, tier = resolve_deep_tier(
        plan, "list gmails to Krishna", None, evidence_pack=EvidencePack(items=[]), turn_retrieval=ctx
    )
    assert use_deep is False
    assert tier == 1


def test_converse_greeting_uses_synth() -> None:
    plan = _plan(intent="converse")
    use_deep, tier = resolve_deep_tier(
        plan, "Hi", None, evidence_pack=EvidencePack(insufficient=True), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is False
    assert tier == 1


def test_yes_after_execute_needs_deep() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting tomorrow", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    plan = _plan(intent="plan")
    use_deep, tier = resolve_deep_tier(
        plan, "yes", hist, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is True
    assert tier == 2


def test_calendar_write_forces_deep() -> None:
    plan = _plan(intent="plan", writes_external=True)
    use_deep, tier = resolve_deep_tier(
        plan, "schedule standup tomorrow 10am", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is True
    assert tier == 2


def test_calendar_read_tier_1() -> None:
    plan = _plan(intent="plan", writes_external=False, domains=["calendar"])
    use_deep, tier = resolve_deep_tier(
        plan, "what's on my calendar tomorrow", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is False
    assert tier == 1


def test_create_summary_tier_1() -> None:
    plan = _plan(intent="plan", writes_external=False)
    use_deep, tier = resolve_deep_tier(
        plan, "create a summary of that mail", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is False
    assert tier == 1


def test_create_meeting_tier_2() -> None:
    plan = _plan(intent="plan", writes_external=True)
    use_deep, tier = resolve_deep_tier(
        plan, "create a meeting with Alex tomorrow", None, evidence_pack=EvidencePack(), turn_retrieval=TurnRetrieval(message="x")
    )
    assert use_deep is True
    assert tier == 2