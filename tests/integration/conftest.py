import pytest

from backend.app import app
from backend.common.config_loader import get_config
from backend.common.config_loader.settings import get_settings
from backend.loader import load_services


@pytest.fixture(autouse=True)
def _services_on_app():
    """TestClient does not always run lifespan — mirror startup service load."""
    cfg = get_config()
    app.state.config = cfg
    app.state.services = load_services(cfg, get_settings())
    yield
