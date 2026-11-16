"""Gmail attachment blobs via gdrive MCP (backend/mcp_servers.json)."""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from backend.services.integrations.mcp.registry import (
    _read_raw_config,
    _silence_stdio_server_banner,
    mcp_adapters_available,
    parse_mcp_servers,
)

_CREATE_TOOL = "drive_create_file"
_READ_TOOL = "drive_read_file"
_READ_CHUNK = 4 * 1024 * 1024
# shortcut: MCP stdio JSON chokes on multi-MB binaries; REST uses same OAuth tokens
_MCP_UPLOAD_MAX_BYTES = 256 * 1024

_tools: dict[str, Any] | None = None


def gdrive_mcp_configured() -> bool:
    for spec in parse_mcp_servers(_read_raw_config()):
        if spec.name == "gdrive" and spec.transport == "stdio":
            return True
    return False


async def _tool_map() -> dict[str, Any]:
    global _tools
    if _tools is not None:
        return _tools
    if not mcp_adapters_available():
        raise RuntimeError("langchain-mcp-adapters not installed")
    conn = None
    for spec in parse_mcp_servers(_read_raw_config()):
        if spec.name == "gdrive" and spec.transport == "stdio":
            conn = spec.connection
            break
    if not conn:
        raise RuntimeError("gdrive MCP missing in mcp_servers.json")
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from backend.common.langsmith_setup import mcp_langsmith_interceptors

    client = MultiServerMCPClient(
        {"gdrive": conn},
        tool_name_prefix=True,
        handle_tool_errors=True,
        tool_interceptors=mcp_langsmith_interceptors(),
    )
    with _silence_stdio_server_banner():
        loaded = await client.get_tools()
    _tools = {}
    for tool in loaded:
        name = tool.name or ""
        if name.endswith(_CREATE_TOOL):
            _tools[_CREATE_TOOL] = tool
        if name.endswith(_READ_TOOL):
            _tools[_READ_TOOL] = tool
    if _CREATE_TOOL not in _tools or _READ_TOOL not in _tools:
        raise RuntimeError("gdrive MCP missing drive_create_file or drive_read_file")
    return _tools


def _jsonish(raw: object) -> object:
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return raw
    return raw


def _file_id(raw: object) -> str:
    data = _jsonish(raw)
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = str(item.get("text") or "")
            if "{" not in text:
                continue
            try:
                payload = json.loads(text[text.index("{") :])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("id"):
                return str(payload["id"])
    if isinstance(data, str) and len(data) >= 8:
        return data
    if isinstance(data, dict):
        for key in ("id", "fileId", "file_id"):
            val = data.get(key)
            if val:
                return str(val)
        nested = data.get("file")
        if isinstance(nested, dict) and nested.get("id"):
            return str(nested["id"])
    raise ValueError(f"gdrive MCP upload returned no id: {raw!r}")


def _read_bytes(raw: object) -> tuple[bytes, int]:
    data = _jsonish(raw)
    if isinstance(data, dict):
        b64 = data.get("contentBase64") or data.get("content_base64") or data.get("data")
        if isinstance(b64, str):
            try:
                chunk = base64.standard_b64decode(b64)
            except binascii.Error:
                chunk = b64.encode()
            n = int(data.get("bytesRead") or data.get("bytes_read") or len(chunk))
            return chunk, n
        text = data.get("content")
        if isinstance(text, str):
            chunk = text.encode()
            return chunk, len(chunk)
    if isinstance(data, str):
        chunk = data.encode()
        return chunk, len(chunk)
    return b"", 0


def _oauth_installed() -> dict[str, str]:
    path = (os.environ.get("GDRIVE_MCP_OAUTH_PATH") or "").strip()
    if not path:
        raise RuntimeError("GDRIVE_MCP_OAUTH_PATH not set")
    raw = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    block = raw.get("installed") or raw.get("web") or {}
    client_id = block.get("client_id") or block.get("clientId")
    client_secret = block.get("client_secret") or block.get("clientSecret")
    if not client_id or not client_secret:
        raise RuntimeError("gdrive oauth json missing client_id/client_secret")
    return {"client_id": str(client_id), "client_secret": str(client_secret)}


def _bearer_token() -> str:
    token_path = (os.environ.get("GDRIVE_MCP_TOKEN_PATH") or "").strip()
    if not token_path:
        raise RuntimeError("GDRIVE_MCP_TOKEN_PATH not set")
    path = Path(token_path).expanduser()
    tokens = json.loads(path.read_text(encoding="utf-8"))
    exp = int(tokens.get("expiry_date") or 0)
    if time.time() * 1000 < exp - 60_000:
        token = tokens.get("access_token")
        if token:
            return str(token)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise RuntimeError("gdrive tokens missing refresh_token; re-run MCP auth")
    oauth = _oauth_installed()
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    merged = {**tokens, **resp.json()}
    if "refresh_token" not in merged:
        merged["refresh_token"] = refresh
    if "expires_in" in merged:
        merged["expiry_date"] = int(time.time() * 1000) + int(merged["expires_in"]) * 1000
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    access = merged.get("access_token")
    if not access:
        raise RuntimeError("token refresh missing access_token")
    return str(access)


async def _drive_rest_upload(*, name: str, mime_type: str, data: bytes) -> str:
    meta = json.dumps({"name": name, "mimeType": mime_type or "application/octet-stream"}).encode()
    boundary = "mindos_drive_boundary"
    body = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
    ).encode() + meta + (
        f"\r\n--{boundary}\r\n"
        f"Content-Type: {mime_type or 'application/octet-stream'}\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    token = _bearer_token()
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id",
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
        )
        resp.raise_for_status()
    file_id = resp.json().get("id")
    if not file_id:
        raise ValueError("drive REST upload missing file id")
    return str(file_id)


async def _drive_mcp_upload(*, name: str, mime_type: str, data: bytes) -> str:
    tools = await _tool_map()
    tool = tools[_CREATE_TOOL]
    payload: dict[str, Any] = {
        "name": name,
        "mimeType": mime_type or "application/octet-stream",
        "content": data.decode("latin-1"),
    }
    with _silence_stdio_server_banner():
        out = await tool.ainvoke(payload)
    return _file_id(out)


async def drive_mcp_upload_bytes(*, name: str, mime_type: str, data: bytes) -> str:
    if len(data) > _MCP_UPLOAD_MAX_BYTES:
        return await _drive_rest_upload(name=name, mime_type=mime_type, data=data)
    try:
        return await _drive_mcp_upload(name=name, mime_type=mime_type, data=data)
    except Exception:
        return await _drive_rest_upload(name=name, mime_type=mime_type, data=data)


async def drive_mcp_download_bytes(file_id: str) -> bytes:
    tools = await _tool_map()
    tool = tools[_READ_TOOL]
    parts: list[bytes] = []
    offset = 0
    while True:
        with _silence_stdio_server_banner():
            out = await tool.ainvoke(
                {"fileId": file_id, "offset": offset, "maxBytes": _READ_CHUNK}
            )
        chunk, n = _read_bytes(out)
        if not chunk:
            break
        parts.append(chunk)
        if n < _READ_CHUNK:
            break
        offset += n
    return b"".join(parts)