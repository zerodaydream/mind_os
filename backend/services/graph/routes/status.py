from fastapi import APIRouter

from backend.services.graph.core.deep_agent import deepagents_available
from backend.services.graph.core.engine import langgraph_available

router = APIRouter(tags=["graph"])


@router.get("/graph/status")
def graph_status():
    return {
        "langgraph": langgraph_available(),
        "deepagents": deepagents_available(),
    }
