"""Fetch a public URL/PDF (via fetch MCP when enabled) and return one LLM summary."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx

from backend.services.agents.core.render.chat_stream import ChatStreamCallback, emit_chat_stream
from backend.services.agents.core.render.prompt_budget import clip_text
from backend.services.integrations.mcp.registry import (
    _read_raw_config,
    _silence_stdio_server_banner,
    mcp_adapters_available,
    parse_mcp_servers,
)

_DOC_CHAR_CAP = 96_000  # shortcut: hard cap; map-reduce summarize when docs exceed this
_ARXIV_PDF = re.compile(r"arxiv\.org/pdf/([^?\s#]+)", re.I)


def _short_url(url: str) -> str:
    u = (url or "").strip()
    if len(u) <= 56:
        return u
    return u[:28] + "…" + u[-20:]


def _parse_fetch_payload(raw: object) -> tuple[str, int | None]:
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return raw.strip(), None
    else:
        return str(raw).strip(), None
    content = str(data.get("content") or data.get("text") or "").strip()
    nxt = data.get("next_start_index")
    if nxt is None and isinstance(data.get("metadata"), dict):
        nxt = data["metadata"].get("next_start_index")
    try:
        nxt_i = int(nxt) if nxt is not None else None
    except (TypeError, ValueError):
        nxt_i = None
    return content, nxt_i


def _fetch_spec_connection() -> dict | None:
    for spec in parse_mcp_servers(_read_raw_config()):
        if spec.name == "fetch" and spec.transport == "stdio":
            return spec.connection
    return None


_FETCH_TOOL = None


async def _load_fetch_tool():
    global _FETCH_TOOL
    if _FETCH_TOOL is not None:
        return _FETCH_TOOL
    if not mcp_adapters_available():
        return None
    conn = _fetch_spec_connection()
    if not conn:
        return None
    from langchain_mcp_adapters.client import MultiServerMCPClient

    from backend.common.langsmith_setup import mcp_langsmith_interceptors

    client = MultiServerMCPClient(
        {"fetch": conn},
        tool_name_prefix=True,
        handle_tool_errors=True,
        tool_interceptors=mcp_langsmith_interceptors(),
    )
    with _silence_stdio_server_banner():
        tools = await client.get_tools()
    for tool in tools:
        if "fetch_url" in (tool.name or ""):
            _FETCH_TOOL = tool
            return tool
    return None


async def _fetch_via_mcp(url: str, *, on_ping: ChatStreamCallback | None) -> str:
    tool = await _load_fetch_tool()
    if tool is None:
        return ""
    parts: list[str] = []
    start = 0
    page = 0
    from backend.services.agents.core.render.tool_status import tool_status_line

    emit_chat_stream(
        on_ping,
        f"[tool] {tool_status_line('mindos_summarize_url')} ({_short_url(url)})…",
        phase="think",
    )
    while len("".join(parts)) < _DOC_CHAR_CAP:
        page += 1
        if page > 1:
            emit_chat_stream(
                on_ping, f"[tool] Reading more of the document (part {page})…", phase="think"
            )
        with _silence_stdio_server_banner():
            payload = await tool.ainvoke({"url": url, "start_index": start})
        chunk, nxt = _parse_fetch_payload(payload)
        if chunk:
            parts.append(chunk)
        if nxt is None or nxt <= start:
            break
        start = nxt
    return clip_text("\n\n".join(parts), _DOC_CHAR_CAP)


async def _arxiv_atom_text(url: str) -> str:
    m = _ARXIV_PDF.search(url)
    if not m:
        return ""
    paper_id = m.group(1).rstrip("/")
    api = f"https://export.arxiv.org/api/query?id_list={paper_id}"
    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            resp = await client.get(api)
            resp.raise_for_status()
            xml = resp.text
    except httpx.HTTPError:
        return ""
    title = ""
    for raw_title in re.findall(r"<title>([^<]+)</title>", xml):
        t = raw_title.strip()
        if "arxiv query" in t.lower():
            continue
        title = t
        break
    entry_m = re.search(r"<entry>(.*?)</entry>", xml, re.S)
    block = entry_m.group(1) if entry_m else xml
    authors = re.findall(r"<name>([^<]+)</name>", block)
    abs_m = re.search(r"<summary>([^<]+)</summary>", block, re.S)
    abstract = re.sub(r"\s+", " ", (abs_m.group(1) if abs_m else "").strip())
    parts = [f"arXiv:{paper_id}"]
    if title:
        parts.append(f"Title: {title}")
    if authors:
        parts.append("Authors: " + ", ".join(authors))
    if abstract:
        parts.append(f"Abstract: {abstract}")
    return "\n\n".join(parts)


async def _fetch_via_httpx(url: str) -> str:
    async with httpx.AsyncClient(follow_redirects=True, timeout=45.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "").lower()
        if "pdf" in ctype:
            return ""
        text = resp.text
        text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return clip_text(text, _DOC_CHAR_CAP)


async def fetch_public_document(url: str, *, on_ping: ChatStreamCallback | None) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    host = urlparse(url).hostname or ""
    if host in {"localhost", "127.0.0.1", "0.0.0.0"}:
        return ""
    body = await _fetch_via_mcp(url, on_ping=on_ping)
    if not body:
        from backend.services.agents.core.render.tool_status import tool_status_line

        emit_chat_stream(
            on_ping,
            f"[tool] {tool_status_line('fetch_url')} ({_short_url(url)})…",
            phase="think",
        )
        body = await _arxiv_atom_text(url)
    if not body:
        try:
            body = await _fetch_via_httpx(url)
        except httpx.HTTPError as exc:
            return f"Fetch failed: {exc}"
    if not body:
        return (
            "Could not read this URL. Enable the fetch MCP in mcp_servers.json for PDFs, "
            "or check the link is public."
        )
    return body


async def summarize_public_url(
    url: str,
    focus: str,
    *,
    gateway,
    on_ping: ChatStreamCallback | None,
) -> str:
    from backend.common.utils.enums import LlmMode, LlmTask

    if gateway is None or gateway.mode == LlmMode.DEGRADED:
        return "Summaries need MINDOS_LLM_MODE=live or mock."
    doc = await fetch_public_document(url, on_ping=on_ping)
    if doc.startswith("Fetch failed") or doc.startswith("Could not read"):
        return doc
    from backend.services.agents.core.render.tool_status import tool_status_line

    emit_chat_stream(on_ping, f"[tool] {tool_status_line('mindos_summarize_url')}…", phase="think")
    focus_line = f"\nFocus: {focus.strip()}" if (focus or "").strip() else ""
    from backend.services.agents.core.render.prompts import summarize_url_reply_shape

    prompt = (
        "Answer from the document below in clear prose for the user. "
        "Use only facts from the source text—never invent numbers, areas, or names. "
        f"{focus_line}\n\n"
        f"{summarize_url_reply_shape()}\n\n"
        f"---\n{doc}\n---\n\n"
    )
    prompt = clip_text(prompt, 120_000, min_keep=8_000)
    try:
        res = await gateway.complete(
            prompt, task=LlmTask.SYNTH, max_output_tokens=2048
        )
    except Exception as exc:
        return f"Summarize failed: {exc}"
    return (res.text or "").strip() or "Empty summary."