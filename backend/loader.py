"""Load enabled service plugins from ``config.json`` and run their lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import logging
from typing import Any

from fastapi import FastAPI

from backend.common.config_loader.schema.base import ConfigResponse
from backend.common.config_loader.settings import Settings
from backend.plugin import Context, ServicePlugin

_logger = logging.getLogger(__name__)
SERVICES_PACKAGE = "backend.services"
API_PREFIX = "/v1"


@dataclass(frozen=True, slots=True)
class LoadedService:
    plugin: ServicePlugin
    entry_settings: dict[str, Any]


@dataclass
class LoadedServices:
    """Runtime view used by routes and the worker."""

    enabled: dict[str, bool] = field(default_factory=dict)
    llm: Any = None
    loaded: list[LoadedService] = field(default_factory=list)


def load_plugin(name: str) -> ServicePlugin:
    module_name = f"{SERVICES_PACKAGE}.{name}"
    module = importlib.import_module(module_name)
    plugin = getattr(module, "plugin", None)
    if not isinstance(plugin, ServicePlugin):
        raise ValueError(f"{module_name} must export ServicePlugin `plugin`")
    if plugin.name != name:
        raise ValueError(f"{module_name} plugin.name={plugin.name!r}, expected {name!r}")
    return plugin


def load_services(config: ConfigResponse, settings: Settings) -> LoadedServices:
    loaded: list[LoadedService] = []
    enabled_map: dict[str, bool] = {}
    for entry in config.services:
        enabled_map[entry.name] = entry.enabled
        if not entry.enabled:
            continue
        plugin = load_plugin(entry.name)
        loaded.append(LoadedService(plugin=plugin, entry_settings=entry.settings))

    by_name = {s.plugin.name: s for s in loaded}
    for service in loaded:
        for dep in service.plugin.depends_on(config.environment):
            if dep not in by_name:
                raise ValueError(f"Service {service.plugin.name!r} requires {dep!r} enabled")

    runtime = LoadedServices(enabled=enabled_map, loaded=loaded)
    if config.is_enabled("llm"):
        from backend.services.llm.core.gateway import build_llm_gateway

        runtime.llm = build_llm_gateway(settings)
    return runtime


def mount_routers(app: FastAPI, config: ConfigResponse) -> None:
    for entry in config.services:
        if not entry.enabled:
            continue
        plugin = load_plugin(entry.name)
        router = plugin.router()
        if router is None:
            continue
        prefix = f"{API_PREFIX}{plugin.prefix}"
        _logger.info("Mounting service=%s prefix=%s", plugin.name, prefix)
        app.include_router(router, prefix=prefix)


def _context(app: FastAPI, config: ConfigResponse, service: LoadedService) -> Context:
    return Context(app=app, environment=config.environment, settings=service.entry_settings)


async def start_services(app: FastAPI, config: ConfigResponse, services: list[LoadedService]) -> None:
    started: list[LoadedService] = []
    try:
        for service in services:
            await service.plugin.startup(_context(app, config, service))
            started.append(service)
    except Exception:
        await stop_services(app, config, started)
        raise


async def stop_services(app: FastAPI, config: ConfigResponse, services: list[LoadedService]) -> None:
    for service in reversed(services):
        try:
            await service.plugin.shutdown(_context(app, config, service))
        except Exception:
            _logger.exception("Shutdown failed for service=%s", service.plugin.name)
