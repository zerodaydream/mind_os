from backend.services.agents.core.turn.orchestrate import handle_turn
from backend.services.graph.core.deep_agent import deepagents_available
from backend.services.graph.core.engine import langgraph_available
from backend.services.integrations.core.calendar.timezone import detect_client_iana_timezone


async def run_agent_pipeline(
    session, user, settings, gateway, prompt: str, *, session_id=None
) -> dict:
    out = await handle_turn(
        session,
        user,
        settings,
        gateway,
        prompt,
        session_id=session_id,
        client_tz=detect_client_iana_timezone(),
    )
    engine = "deepagents" if deepagents_available() else (
        "langgraph" if langgraph_available() else "linear"
    )
    return {
        "engine": engine,
        "prompt": out["message"],
        "results": out.get("sources") or [],
        "answer": out["answer"],
        "mode": out["mode"],
        "intent": out.get("intent"),
        "session_id": out.get("session_id"),
    }
