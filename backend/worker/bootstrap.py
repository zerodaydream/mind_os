from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.common.database.session import init_db
from backend.loader import LoadedServices, load_services

_services: LoadedServices | None = None


def bootstrap() -> LoadedServices:
    global _services
    settings = get_settings()
    init_db(settings.database_url)
    config = get_config()
    _services = load_services(config, settings)
    return _services


def worker_services() -> LoadedServices | None:
    return _services
