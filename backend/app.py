from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request

from backend.common.api.deps import app_settings
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import Settings, get_settings
from backend.common.database.lifecycle import dispose_db
from backend.common.database.session import init_db
from backend.common.logger import setup_logging
from backend.common.ssl_env import fix_ssl_cert_env
from backend.common.middleware.rate_limit import RateLimitMiddleware
from backend.loader import load_services, mount_routers, start_services, stop_services

health_router = APIRouter(tags=["health"])
meta_router = APIRouter(tags=["meta"])


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@meta_router.get("/meta")
async def meta(request: Request, cfg: Settings = Depends(app_settings)) -> dict:
    services = request.app.state.services
    return {
        "version": "0.1.0",
        "llm_mode": cfg.mindos_llm_mode.value,
        "llm_provider": cfg.mindos_default_provider,
        "llm_extract_model": cfg.mindos_extract_model,
        "llm_synth_model": cfg.mindos_synth_model,
        "services_enabled": services.enabled,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db(settings.database_url)
    config = app.state.config
    app.state.services = load_services(config, settings)
    try:
        await start_services(app, config, app.state.services.loaded)
    except Exception as exc:
        raise SystemExit(1) from exc
    yield
    await stop_services(app, config, app.state.services.loaded)
    await dispose_db()


def create_app() -> FastAPI:
    setup_logging()
    config = get_config()
    application = FastAPI(title="MindOS", version="0.1.0", lifespan=lifespan)
    application.state.config = config
    application.add_middleware(RateLimitMiddleware, max_per_minute=60)
    application.include_router(health_router)
    application.include_router(meta_router, prefix="/v1")
    mount_routers(application, config)
    return application


app = create_app()
