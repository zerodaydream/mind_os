import pytest

from backend.services.graph.core.deep_agent import deepagents_available


def test_deepagents_available_is_bool() -> None:
    assert isinstance(deepagents_available(), bool)


@pytest.mark.skipif(not deepagents_available(), reason="deepagents extra not installed")
def test_deepagents_import_smoke() -> None:
    from deepagents import create_deep_agent  # noqa: F401
    from langchain.agents.middleware import TodoListMiddleware  # noqa: F401
