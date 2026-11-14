from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.memories.routes import list_memories


class MemoriesPlugin(ServicePlugin):
    name: ClassVar[str] = "memories"

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(list_memories.router)
        return router


plugin = MemoriesPlugin()
