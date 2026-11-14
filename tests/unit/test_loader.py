from backend.common.config_loader.schema.base import ConfigResponse, ServiceEntry
from backend.common.config_loader.settings import Settings
from backend.loader import load_services


def test_llm_off_skips_gateway() -> None:
    cfg = ConfigResponse(services=[ServiceEntry(name="llm", enabled=False)])
    loaded = load_services(cfg, Settings())
    assert loaded.llm is None
    assert loaded.enabled["llm"] is False


def test_llm_on_builds_gateway() -> None:
    cfg = ConfigResponse(services=[ServiceEntry(name="llm", enabled=True)])
    loaded = load_services(cfg, Settings())
    assert loaded.llm is not None
