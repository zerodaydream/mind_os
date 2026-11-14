from fastapi.testclient import TestClient

from backend.app import app


def test_health() -> None:
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}


def test_meta_lists_services() -> None:
    client = TestClient(app)
    body = client.get("/v1/meta").json()
    assert "services_enabled" in body
    assert "llm" in body["services_enabled"]
