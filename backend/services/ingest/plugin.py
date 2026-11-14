from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.ingest.routes import manual


class IngestPlugin(ServicePlugin):
    name: ClassVar[str] = "ingest"

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(manual.router)
        return router


plugin = IngestPlugin()
