"""FastAPI plugin: graph status routes."""

from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.graph.routes import status


class GraphPlugin(ServicePlugin):
    name: ClassVar[str] = "graph"

    def router(self) -> APIRouter:
        """Mount service routes on the FastAPI app."""
        router = APIRouter()
        router.include_router(status.router)
        return router


plugin = GraphPlugin()
