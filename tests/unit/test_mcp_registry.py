from backend.services.integrations.mcp.registry import (
    _personal_pack_enabled,
    mcp_guidance_prompt,
    parse_mcp_servers,
)


def test_requires_env_skips_server(monkeypatch) -> None:
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    raw = {
        "servers": [
            {
                "name": "brave",
                "enabled": True,
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "pkg"],
                "requires_env": ["BRAVE_API_KEY"],
                "env": {"BRAVE_API_KEY": "$BRAVE_API_KEY"},
                "guidance": "web",
            }
        ]
    }
    assert parse_mcp_servers(raw) == []
    monkeypatch.setenv("BRAVE_API_KEY", "test-key")
    specs = parse_mcp_servers(raw)
    assert len(specs) == 1
    assert specs[0].connection["env"]["BRAVE_API_KEY"] == "test-key"


def test_parse_builtin_stdio_and_http() -> None:
    raw = {
        "servers": [
            {
                "name": "personal",
                "enabled": True,
                "transport": "builtin",
                "guidance": "mail",
            },
            {
                "name": "remote",
                "enabled": True,
                "transport": "http",
                "url": "http://127.0.0.1:9/mcp",
                "guidance": "remote tools",
            },
            {"name": "bad name!", "transport": "stdio"},
            {"name": "off", "enabled": False, "transport": "stdio", "args": []},
        ]
    }
    specs = parse_mcp_servers(raw)
    names = {s.name for s in specs}
    assert names == {"personal", "remote"}
    personal = next(s for s in specs if s.name == "personal")
    assert personal.connection["transport"] == "builtin"


def test_personal_pack_default_when_config_missing() -> None:
    assert _personal_pack_enabled([], None) is True
    assert _personal_pack_enabled([], {"servers": []}) is True


def test_mcp_guidance_prompt_lists_servers() -> None:
    specs = parse_mcp_servers(
        {
            "servers": [
                {
                    "name": "personal",
                    "enabled": True,
                    "transport": "builtin",
                    "guidance": "When user asks about mail or calendar.",
                }
            ]
        }
    )
    block = mcp_guidance_prompt(specs)
    assert "personal" in block
    assert "mail or calendar" in block