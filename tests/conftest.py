import pytest

from backend.common.config_loader import get_config, reload_config
from backend.common.utils.enums import LlmMode
from backend.common.config_loader.settings import Settings, get_settings
from backend.services.llm.core.gateway import build_llm_gateway


@pytest.fixture(autouse=True)
def _clear_caches():
    get_settings.cache_clear()
    reload_config()
    yield
    get_settings.cache_clear()
    reload_config()


@pytest.fixture
def mock_gateway():
    settings = Settings(mindos_llm_mode=LlmMode.MOCK)
    return build_llm_gateway(settings)
