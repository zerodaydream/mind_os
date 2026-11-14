"""Contract every service package exports as its module-level ``plugin``."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

_logger = logging.getLogger(__name__)
_RESERVED_STATE = frozenset({"config", "services"})


@dataclass(frozen=True, slots=True)
class Context:
    app: FastAPI
    environment: str
    settings: Any

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    def use(self, name: str) -> Any:
        value = getattr(self.app.state, name, None)
        if value is None:
            raise RuntimeError(f"Service {name!r} is not available")
        return value

    def provide(self, name: str, value: Any) -> None:
        if name in _RESERVED_STATE:
            raise ValueError(f"app.state.{name} is reserved")
        setattr(self.app.state, name, value)
        _logger.info("Provided app.state.%s", name)


class ServicePlugin:
    name: ClassVar[str]
    prefix: ClassVar[str] = ""

    def depends_on(self, _environment: str) -> tuple[str, ...]:
        return ()

    def starts_after(self, _environment: str) -> tuple[str, ...]:
        return ()

    def router(self) -> APIRouter | None:
        return None

    async def startup(self, ctx: Context) -> None:
        pass

    async def shutdown(self, ctx: Context) -> None:
        pass
