from backend.services.agents.core.render.tool_status import tool_status_line


def test_search_label() -> None:
    assert tool_status_line("mindos_search") == (
        "Searching your mail and indexed documents"
    )


def test_trace_suffix_stripped() -> None:
    assert "Searching" in tool_status_line("mindos_search_deadbeef12")