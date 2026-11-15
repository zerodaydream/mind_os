"""Dynamic MCP servers from JSON — loaded into the deep agent tool list."""

from backend.services.integrations.mcp.registry import load_mcp_for_agent

__all__ = ["load_mcp_for_agent"]