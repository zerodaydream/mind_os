from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.agents.routes import chat, run, sessions


class AgentsPlugin(ServicePlugin):
    name: ClassVar[str] = "agents"

    def depends_on(self, _environment: str) -> tuple[str, ...]:
        return ("graph", "llm", "retrieval")

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(run.router)
        router.include_router(chat.router)
        router.include_router(sessions.router)
        return router


plugin = AgentsPlugin()
