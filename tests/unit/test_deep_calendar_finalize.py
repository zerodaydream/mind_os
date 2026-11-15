from backend.services.graph.core.deep_agent import (
    _answer_leaks_system_prompt,
    _finalize_calendar_answer,
    _replace_relative_oauth_paths,
)


def test_detect_prompt_leak() -> None:
    assert _answer_leaks_system_prompt("If calendar tools report auth errors, follow…")


def test_relative_oauth_path() -> None:
    url = "http://127.0.0.1:8000/v1/integrations/google/start"
    out = _replace_relative_oauth_paths("visit `/v1/integrations/google/start`", url)
    assert url in out
    assert "/v1/integrations" not in out or url in out


def test_finalize_prefers_tool_auth_error() -> None:
    result = {
        "messages": [
            {"type": "tool", "content": "Calendar list failed: Calendar permission missing"},
            {"type": "ai", "content": "Please visit /v1/integrations/google/start"},
        ]
    }
    ans = _finalize_calendar_answer(
        "Please visit /v1/integrations/google/start",
        result,
        settings=None,
        auth_status="ok",
        oauth_url="http://127.0.0.1:8000/v1/integrations/google/start",
        calendar_turn=True,
    )
    assert "Reply **done**" in ans
    assert "127.0.0.1:8000" in ans


def test_finalize_created_events_not_leak() -> None:
    result = {
        "messages": [
            {
                "type": "tool",
                "content": "Created calendar event: A @ Mon 1pm (id=x)",
            },
            {
                "type": "ai",
                "content": "If calendar tools report auth errors…",
            },
        ]
    }
    ans = _finalize_calendar_answer(
        "If calendar tools report auth errors…",
        result,
        settings=None,
        auth_status="ok",
        oauth_url="http://127.0.0.1:8000/v1/integrations/google/start",
        calendar_turn=True,
    )
    assert ans.startswith("Created calendar event")