from backend.services.agents.core.routing.turn_prep import TurnPrep
from backend.services.agents.core.turn.orchestrate import needs_deep_agent
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


def test_execute_intent_needs_deep() -> None:
    assert needs_deep_agent(_prep("execute"), "list calendar", None) is True


def test_slash_command_needs_deep() -> None:
    assert needs_deep_agent(_prep("plan"), "/do sync gmail", None) is True


def test_plan_list_mail_uses_synth() -> None:
    ctx = TurnRetrieval(message="x", has_context=True)
    assert needs_deep_agent(
        _prep("plan", prefetch_mail=True),
        "list gmails to Krishna",
        None,
        ctx,
    ) is False


def test_converse_greeting_uses_synth() -> None:
    assert needs_deep_agent(_prep("converse"), "Hi", None) is False


def test_yes_after_execute_needs_deep() -> None:
    hist = [
        {"role": "user", "content": "schedule meeting tomorrow", "intent": "execute"},
        {"role": "assistant", "content": "What time?", "intent": "execute"},
    ]
    assert needs_deep_agent(_prep("plan"), "yes", hist) is True


def test_schedule_verbs_need_deep() -> None:
    assert needs_deep_agent(_prep("plan"), "schedule standup tomorrow 10am", None) is True


def test_named_attachment_without_context_needs_deep() -> None:
    assert needs_deep_agent(
        _prep("plan"),
        "summarize Propico deck.pdf from mail",
        None,
        TurnRetrieval(message="x", has_context=False),
    ) is True


def test_named_attachment_with_context_uses_synth() -> None:
    assert needs_deep_agent(
        _prep("plan"),
        "summarize Propico deck.pdf from mail",
        None,
        TurnRetrieval(message="x", has_context=True, gmail_ctx=[{"title": "x"}]),
    ) is False
