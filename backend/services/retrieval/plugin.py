from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.retrieval.routes import search


class RetrievalPlugin(ServicePlugin):
    name: ClassVar[str] = "retrieval"

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(search.router)
        return router


plugin = RetrievalPlugin()
