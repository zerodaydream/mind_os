"""Load MCP server definitions from JSON and expose LangChain tools to the agent."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)
# MCP JSON Schema uses $schema/additionalProperties; Gemini adapter warns per field.
logging.getLogger("langchain_google_genai._function_utils").setLevel(logging.ERROR)

_SERVER_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_ENV_REF = re.compile(r"^\$[{]?([A-Za-z_][A-Za-z0-9_]*)[}]?$")
_MCP_STDIO_LABELS = {"fetch": "web fetch"}


def mcp_loading_user_message(specs: list[McpServerSpec]) -> str:
    stdio = [s.name for s in specs if s.transport == "stdio"]
    if not stdio:
        return "Connecting external tools…"
    label = ", ".join(_MCP_STDIO_LABELS.get(n, n) for n in stdio)
    return f"Connecting {label} (may take a few seconds on first load)…"


@contextlib.contextmanager
def _silence_stdio_server_banner():
    """Hide npm MCP servers that log to stderr on startup (e.g. mcp-web-search)."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved = os.dup(2)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    enabled: bool
    transport: str
    guidance: str
    connection: dict[str, Any]


def _default_mcp_path() -> Path:
    if env := os.getenv("MINDOS_MCP_CONFIG_PATH"):
        return Path(env)
    return Path(__file__).resolve().parents[3] / "mcp_servers.json"


@lru_cache
def _read_raw_config() -> dict[str, Any] | None:
    path = _default_mcp_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("MCP config unreadable at %s: %s", path, exc)
        return None


def _resolve_command(command: str) -> str:
    cmd = (command or "").strip()
    if not cmd or cmd in {"@python@", "{python}"}:
        return sys.executable
    return cmd


def _env_present(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _resolve_env_value(value: str) -> str:
    raw = (value or "").strip()
    m = _ENV_REF.match(raw)
    if m:
        return (os.environ.get(m.group(1)) or "").strip()
    return raw


def _required_env_ok(item: dict[str, Any]) -> bool:
    req = item.get("requires_env") or []
    if not isinstance(req, list):
        return True
    missing = [str(k).strip() for k in req if str(k).strip() and not _env_present(str(k))]
    if missing:
        logger.info(
            "MCP server %s skipped (set env: %s)",
            item.get("name"),
            ", ".join(missing),
        )
        return False
    return True


def parse_mcp_servers(raw: dict[str, Any] | None) -> list[McpServerSpec]:
    if not raw:
        return []
    entries = raw.get("servers")
    if not isinstance(entries, list):
        return []
    specs: list[McpServerSpec] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip().lower()
        if not _SERVER_NAME.match(name):
            logger.warning("MCP server skipped (bad name): %r", name)
            continue
        if not item.get("enabled", True):
            continue
        if not _required_env_ok(item):
            continue
        transport = str(item.get("transport") or "stdio").strip().lower()
        guidance = str(item.get("guidance") or "").strip()
        connection: dict[str, Any]
        if transport == "builtin":
            connection = {"transport": "builtin"}
        elif transport == "stdio":
            args = item.get("args") or []
            if not isinstance(args, list):
                args = []
            env = item.get("env") or {}
            if not isinstance(env, dict):
                env = {}
            resolved_env = {
                str(k): _resolve_env_value(str(v)) for k, v in env.items() if str(k).strip()
            }
            cmd = _resolve_command(str(item.get("command") or ""))
            if cmd.endswith("npx") or "npx" in str(item.get("command") or ""):
                resolved_env.setdefault("NODE_NO_WARNINGS", "1")
                resolved_env.setdefault("NPM_CONFIG_LOGLEVEL", "silent")
                resolved_env.setdefault("NO_COLOR", "1")
                resolved_env.setdefault("FORCE_COLOR", "0")
            connection = {
                "transport": "stdio",
                "command": cmd,
                "args": [str(a) for a in args],
                "env": resolved_env,
            }
        elif transport in ("http", "sse", "streamable_http"):
            url = str(item.get("url") or "").strip()
            if not url:
                logger.warning("MCP server %s missing url", name)
                continue
            connection = {"transport": transport, "url": url}
        else:
            logger.warning("MCP server %s unsupported transport %s", name, transport)
            continue
        specs.append(
            McpServerSpec(
                name=name,
                enabled=True,
                transport=transport,
                guidance=guidance,
                connection=connection,
            )
        )
    return specs


def mcp_guidance_prompt(servers: list[McpServerSpec]) -> str:
    lines = [f"- {s.name}: {s.guidance}" for s in servers if s.guidance]
    if not lines:
        return ""
    return (
        "Tool packs from backend/mcp_servers.json (external tools may be prefixed). Match guidance:\n"
        + "\n".join(lines)
    )


def _personal_pack_enabled(specs: list[McpServerSpec], raw: dict[str, Any] | None) -> bool:
    if raw is None:
        return True
    builtins = [s for s in specs if s.transport == "builtin"]
    if not builtins:
        return not (raw.get("servers") or [])
    return True


def mcp_adapters_available() -> bool:
    try:
        import langchain_mcp_adapters  # noqa: F401

        return True
    except ImportError:
        return False


async def load_mcp_for_agent(
    session: AsyncSession,
    user,
    settings,
    *,
    default_tz: str = "UTC",
    user_task: str = "",
    gateway=None,
    on_partial=None,
) -> tuple[list[Any], str]:
    """Return (tools, extra_system_prompt_fragment). Built-in packs use the agent DB session."""
    raw = _read_raw_config()
    specs = parse_mcp_servers(raw)
    guidance = mcp_guidance_prompt(specs)
    tools: list[Any] = []

    builtin_names = {s.name for s in specs if s.transport == "builtin"}
    if _personal_pack_enabled(specs, raw) and ("personal" in builtin_names or not builtin_names):
        from backend.services.integrations.mcp.personal_tools import build_personal_tools

        tools.extend(
            build_personal_tools(
                session,
                user,
                settings,
                default_tz=default_tz,
                user_task=user_task,
                gateway=gateway,
                on_partial=on_partial,
            )
        )
    if "filesystem" in builtin_names:
        from backend.services.integrations.mcp.filesystem_tools import (
            build_filesystem_tools,
            filesystem_enabled,
        )

        if filesystem_enabled():
            tools.extend(build_filesystem_tools(on_partial=on_partial, user_task=user_task))
    if "convert" in builtin_names:
        from backend.services.integrations.mcp.convert_tools import (
            build_convert_tools,
            convert_enabled,
        )

        if convert_enabled():
            tools.extend(build_convert_tools(user_task=user_task))

    # shortcut: stdio MCP (fetch) not mounted on the agent; mindos_summarize_url loads fetch on demand

    return tools, guidance