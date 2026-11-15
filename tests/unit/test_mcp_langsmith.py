import pytest

from backend.common.langsmith_setup import McpLangSmithInterceptor


class _Req:
    server_name = "fetch"
    name = "fetch_url"
    args = {"url": "https://example.com"}


@pytest.mark.asyncio
async def test_mcp_interceptor_passthrough_when_tracing_off(monkeypatch) -> None:
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)

    async def handler(req):
        return "ok"

    out = await McpLangSmithInterceptor()(_Req(), handler)
    assert out == "ok"