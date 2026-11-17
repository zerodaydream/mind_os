import pytest

from backend.services.agents.core.routing.router_llm import resolve_turn_routing


@pytest.mark.asyncio
async def test_resolve_turn_routing_skips_llm_without_history() -> None:
    intent, body = await resolve_turn_routing(None, "show my inbox", None)
    assert intent == "plan"
    assert body == "show my inbox"