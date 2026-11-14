from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.tensions.routes import list_tensions, patch_tension


class TensionsPlugin(ServicePlugin):
    name: ClassVar[str] = "tensions"

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(list_tensions.router)
        router.include_router(patch_tension.router)
        return router


plugin = TensionsPlugin()
