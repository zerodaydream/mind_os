from backend.services.integrations.mcp.summarize_url import _parse_fetch_payload
from backend.services.graph.core.deep_agent import _tool_think_line


def test_parse_fetch_payload_pagination() -> None:
    chunk, nxt = _parse_fetch_payload(
        '{"content": "hello", "next_start_index": 1200}'
    )
    assert chunk == "hello"
    assert nxt == 1200
    chunk2, nxt2 = _parse_fetch_payload("plain text only")
    assert chunk2 == "plain text only"
    assert nxt2 is None


def test_tool_think_summarize() -> None:
    line = _tool_think_line(
        "mindos_summarize_url",
        {"url": "https://example.com/paper.pdf"},
    )
    assert "summar" in line.lower()
    assert "example.com" in line