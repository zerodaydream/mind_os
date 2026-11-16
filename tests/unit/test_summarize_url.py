from backend.services.integrations.mcp.summarize_url import _parse_fetch_payload
from backend.services.graph.core.deep_agent import _canonical_tool_name


def test_parse_fetch_payload_pagination() -> None:
    chunk, nxt = _parse_fetch_payload(
        '{"content": "hello", "next_start_index": 1200}'
    )
    assert chunk == "hello"
    assert nxt == 1200
    chunk2, nxt2 = _parse_fetch_payload("plain text only")
    assert chunk2 == "plain text only"
    assert nxt2 is None


def test_canonical_tool_name_strips_trace_suffix() -> None:
    assert _canonical_tool_name("mindos_convert_file_8dcad8f2c0") == "mindos_convert_file"