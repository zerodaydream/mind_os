from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter, Depends

from backend.common.api.deps import require_integrations
from backend.plugin import ServicePlugin
from backend.services.integrations.routes import google


class IntegrationsPlugin(ServicePlugin):
    name: ClassVar[str] = "integrations"

    def router(self) -> APIRouter:
        router = APIRouter(dependencies=[Depends(require_integrations)])
        router.include_router(google.router)
        return router


plugin = IntegrationsPlugin()
