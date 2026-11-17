from backend.services.agents.core.render.tool_status import tool_status_line


def test_search_label() -> None:
    assert tool_status_line("mindos_mail_lookup") == "Loading indexed Gmail messages"
    assert tool_status_line("mindos_search") == "Searching the indexed documents"


def test_trace_suffix_stripped() -> None:
    assert "Searching" in tool_status_line("mindos_search_deadbeef12")