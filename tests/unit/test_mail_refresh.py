from backend.services.agents.core.turn_context import (
    TurnContext,
    preflight_prompt_block,
    should_refresh_mail_index,
)


def test_refresh_on_empty_hybrid_hits() -> None:
    ctx = TurnContext(message="find gmail from recruiter", hybrid_hit_count=0)
    assert should_refresh_mail_index("find gmail from recruiter", ctx, source=None) is True


def test_skip_when_hybrid_hits() -> None:
    ctx = TurnContext(message="find indeed mail", hybrid_hit_count=3)
    assert should_refresh_mail_index("find indeed mail", ctx, source=None) is False


def test_skip_refresh_when_gmail_ctx_without_hybrid() -> None:
    ctx = TurnContext(
        message="dates when sent",
        hybrid_hit_count=0,
        gmail_ctx=[{"title": "Re: test", "artifact_id": "1"}],
        has_context=True,
    )
    assert should_refresh_mail_index("when were they sent", ctx, source=None) is False


def test_explicit_sync_overrides_hits() -> None:
    ctx = TurnContext(message="sync", hybrid_hit_count=5)
    assert should_refresh_mail_index("sync my gmail inbox", ctx, source=None) is True


def test_non_mail_never_refreshes() -> None:
    ctx = TurnContext(message="hello", hybrid_hit_count=0)
    assert should_refresh_mail_index("hello", ctx, source=None) is False


def test_preflight_prompt_block_nonempty() -> None:
    block = preflight_prompt_block(["I'll sync Gmail.", "Gmail sync finished (ingested 2)."])
    assert "Steps already taken" in block
    assert "sync Gmail" in block