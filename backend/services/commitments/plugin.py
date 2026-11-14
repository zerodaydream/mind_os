from __future__ import annotations

from typing import ClassVar

from fastapi import APIRouter

from backend.plugin import ServicePlugin
from backend.services.commitments.routes import commitments


class CommitmentsPlugin(ServicePlugin):
    name: ClassVar[str] = "commitments"

    def router(self) -> APIRouter:
        router = APIRouter()
        router.include_router(commitments.router)
        return router


plugin = CommitmentsPlugin()
